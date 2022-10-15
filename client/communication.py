import asyncio, logginh, struct, bitstring

from asyncio import Queue
from concurrent.futures import CancelledError



REQUEST_SIZE = 2**14


# Handles the downloading/uploaded of a torrent's pieces.
# Retrieves an available peer from the queue, attempts to open a connection, and perfoms a BitTorrent 'handshake' to the peer.
# Once the handshake is initiated, the endpoint cant request any more data from remote peers until a response is returned from the peer.
# If the connection drops, the endpoint moves on to the next available peer in the queue.
class PeerConnection:
    def __init__(self, queue: Queue, info_hash,
                 peer_id, piece_manager, on_block_cb=None):
                     
        # Initializes a PeerConnection and adds it to the asyncio event-loop.
        # Calls 'stop' to abort the connection.
        
        # queue: The queue
        # info_hash: SHA1 hash for the meta-data.
        # peer_id: Unique IDs for th epeers.
        # piece_manager: Determines what pieces need to be requested.
        # on_block_cb: Called when a block is retrieved from a remote peer.
        self.my_state = []
        self.peer_state = []
        self.queue = queue
        self.info_hash = info_hash
        self.peer_id = peer_id
        self.remote_id = None
        self.writer = None
        self.reader = None
        self.piece_manager = piece_manager
        self.on_block_cb = on_block_cb
        self.future = asyncio.ensure_future(self._start())

    async def _start(self):
        while 'stopped' not in self.my_state:
            ip, port = await self.queue.get()
            logging.info('Assigned to peer - {ip}'.format(ip=ip))

            try:
                self.reader, self.writer = await asyncio.open_connection(
                    ip, port)
                logging.info('Connection open with peer: {ip}'.format(ip=ip))
                buffer = await self._handshake()

                self.my_state.append('stalled')

                await self._send_interested()
                self.my_state.append('interested')

                async for message in PeerStreamIterator(self.reader, buffer):
                    if 'stopped' in self.my_state:
                        break
                    if type(message) is BitField:
                        self.piece_manager.add_peer(self.remote_id,
                                                    message.bitfield)
                    elif type(message) is Interested:
                        self.peer_state.append('interested')
                    elif type(message) is NotInterested:
                        if 'interested' in self.peer_state:
                            self.peer_state.remove('interested')
                    elif type(message) is stall:
                        self.my_state.append('stalled')
                    elif type(message) is Unstall:
                        if 'stalled' in self.my_state:
                            self.my_state.remove('stalled')
                    elif type(message) is Have:
                        self.piece_manager.update_peer(self.remote_id,
                                                       message.index)
                    elif type(message) is KeepAlive:
                        pass
                    elif type(message) is Piece:
                        self.my_state.remove('pending_request')
                        self.on_block_cb(
                            peer_id=self.remote_id,
                            piece_index=message.index,
                            block_offset=message.begin,
                            data=message.block)
                    elif type(message) is Request:
                        logging.info('Ignoring the received Request message.')
                    elif type(message) is Cancel:
                        logging.info('Ignoring the received Cancel message.')

                    # Sends a block to remote peers we're interested in.
                    if 'stalled' not in self.my_state:
                        if 'interested' in self.my_state:
                            if 'pending_request' not in self.my_state:
                                self.my_state.append('pending_request')
                                await self._request_piece()

            except ProtocolError as e:
                logging.exception('Protocol error')
            except (ConnectionRefusedError, TimeoutError):
                logging.warning('Unable to connect to peer')
            except (ConnectionResetError, CancelledError):
                logging.warning('Connection closed')
            except Exception as e:
                logging.exception('An error occurred')
                self.cancel()
                raise e
            self.cancel()

    # Tells the remote peer that there's an impeding connection closure, then closes the connection.
    def cancel(self):
        logging.info('Closing peer {id}'.format(id=self.remote_id))
        if not self.future.done():
            self.future.cancel()
        if self.writer:
            self.writer.close()

        self.queue.task_done()

    # Stops the connection with the active peer, if one exists.
    def stop(self):

        # Sets the state to 'stopped'
        self.my_state.append('stopped')
        if not self.future.done():
            self.future.cancel()

    async def _request_piece(self):
        block = self.piece_manager.next_request(self.remote_id)
        if block:
            message = Request(block.piece, block.offset, block.length).encode()

            logging.debug('Requesting block {block} for piece: {piece} of {length} bytes from peer {peer}'.format(
                            piece=block.piece,
                            block=block.offset,
                            length=block.length,
                            peer=self.remote_id))

            self.writer.write(message)
            await self.writer.drain()
   
    # Sends the initial handshake to the remote peer and waits for the peer to respond.
    async def _handshake(self):
        self.writer.write(Handshake(self.info_hash, self.peer_id).encode())
        await self.writer.drain()

        buf = b''
        tries = 1
        while len(buf) < Handshake.length and tries < 10:
            tries += 1
            buf = await self.reader.read(PeerStreamIterator.CHUNK_SIZE)

        response = Handshake.decode(buf[:Handshake.length])
        if not response:
            raise ProtocolError('Unable parse handshake')
        if not response.info_hash == self.info_hash:
            raise ProtocolError('Handshake with invalid info_hash')

        self.remote_id = response.peer_id
        logging.info('Successfully shook the peer\'s hand.')

        return buf[Handshake.length:]

    async def _send_interested(self):
        message = Interested()
        logging.debug('Sending message: {type}'.format(type=message))
        self.writer.write(message.encode())
        await self.writer.drain()


# An async iterator that reads from the specified stream reader and tries to parse valid BitTorrent messages from that stream of bytes.
class PeerStreamIterator:
    CHUNK_SIZE = 10*1024

    def __init__(self, reader, initial: bytes=None):
        self.reader = reader
        self.buffer = initial if initial else b''

    async def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            try:
                data = await self.reader.read(PeerStreamIterator.CHUNK_SIZE)
                if data:
                    self.buffer += data
                    message = self.parse()
                    if message:
                        return message
                else:
                    logging.debug('No data to read.')
                    if self.buffer:
                        message = self.parse()
                        if message:
                            return message
                    raise StopAsyncIteration()
            except ConnectionResetError:
                logging.debug('Connection closed by peer.')
                raise StopAsyncIteration()
            except CancelledError:
                raise StopAsyncIteration()
            except StopAsyncIteration as e:
                raise e
            except Exception:
                logging.exception('Error')
                raise StopAsyncIteration()
        raise StopAsyncIteration()

    # Tries to parse protocol messages if there are enough bytes to read in the buffer.
    # Returns the parsed message, or None.
    def parse(self):
        # Message structure: <length prefix><message ID><payload>
        # length prefix': a 4 byte big-endian value
        # message ID: a decimal byte
        # payload: the value of 'length prefix'
        header_length = 4

        if len(self.buffer) > 4:
            message_length = struct.unpack('>I', self.buffer[0:4])[0]

            if message_length == 0:
                return KeepAlive()

            if len(self.buffer) >= message_length:
                message_id = struct.unpack('>b', self.buffer[4:5])[0]

                def _consume():
                    self.buffer = self.buffer[header_length + message_length:]

                def _data():
                    return self.buffer[:header_length + message_length]

                if message_id is PeerMessage.BitField:
                    data = _data()
                    _consume()
                    return BitField.decode(data)
                elif message_id is PeerMessage.Interested:
                    _consume()
                    return Interested()
                elif message_id is PeerMessage.NotInterested:
                    _consume()
                    return NotInterested()
                elif message_id is PeerMessage.stall:
                    _consume()
                    return stall()
                elif message_id is PeerMessage.Unstall:
                    _consume()
                    return Unstall()
                elif message_id is PeerMessage.Have:
                    data = _data()
                    _consume()
                    return Have.decode(data)
                elif message_id is PeerMessage.Piece:
                    data = _data()
                    _consume()
                    return Piece.decode(data)
                elif message_id is PeerMessage.Request:
                    data = _data()
                    _consume()
                    return Request.decode(data)
                elif message_id is PeerMessage.Cancel:
                    data = _data()
                    _consume()
                    return Cancel.decode(data)
                else:
                    logging.info('Error')
            else:
                logging.debug('Error')
        return None


# A message between a pair of peers (badum tiss).
class PeerMessage:
    stall = 0
    Unstall = 1
    Interested = 2
    NotInterested = 3
    Have = 4
    BitField = 5
    Request = 6
    Piece = 7
    Cancel = 8
    Port = 9
    Handshake = None 
    KeepAlive = None


# The handshake is a 68 byte long message sent by a remote peer. is the first message sent and then received from a
# Format: <pstrlen><pstr><reserved><info_hash><peer_id>
class Handshake(PeerMessage):
    length = 49 + 19

    def __init__(self, info_hash: bytes, peer_id: bytes):
        # info_hash: The SHA1 hash for the info dict
        # peer_id: The unique ID for each peer.
        if isinstance(info_hash, str):
            info_hash = info_hash.encode('utf-8')
        if isinstance(peer_id, str):
            peer_id = peer_id.encode('utf-8')
        self.info_hash = info_hash
        self.peer_id = peer_id

    
    # Encodes the object instance to a byte representation of the message.
    def encode(self) -> bytes:
        return struct.pack(
            '>B19s8x20s20s',
            19,                         # Single byte (B)
            b'BitTorrent protocol',     # String 19s
                                        # Reserved 8x (pad byte, no value)
            self.info_hash,             # String 20s
            self.peer_id)               # String 20s

    # Decodes the BitTorrent message into a handshake, if its not valid it returns None.
    @classmethod
    def decode(cls, data: bytes):
        logging.debug('Decoding handshake of length: {length}'.format(
            length=len(data)))
        if len(data) < (49 + 19):
            return None
        parts = struct.unpack('>B19s8x20s20s', data)
        return cls(info_hash=parts[2], peer_id=parts[3])

    def __str__(self):
        return 'Handshake'


class KeepAlive(PeerMessage):
    def __str__(self):
        return 'KeepAlive'


# The BitField is a message whose payload is a bit array representing the bits that a peer does or does not have.
# Format: <len=0001+X><id=5><bitfield>
class BitField(PeerMessage):
    def __init__(self, data):
        self.bitfield = bitstring.BitArray(bytes=data)

    def encode(self) -> bytes:
        bits_length = len(self.bitfield)
        return struct.pack('>Ib' + str(bits_length) + 's',
                           1 + bits_length,
                           PeerMessage.BitField,
                           self.bitfield)

    @classmethod
    def decode(cls, data: bytes):
        message_length = struct.unpack('>I', data[:4])[0]
        logging.debug('Decoding BitField of length: {length}'.format(
            length=message_length))

        parts = struct.unpack('>Ib' + str(message_length - 1) + 's', data)
        return cls(parts[2])

    def __str__(self):
        return 'BitField'


class Interested(PeerMessage):
    # format: <len=0001><id=2>

    def encode(self) -> bytes:
        return struct.pack('>Ib',
                           1,  # Message length
                           PeerMessage.Interested)

    def __str__(self):
        return 'Interested'


class NotInterested(PeerMessage):
    # format: <len=0001><id=3>
    def __str__(self):
        return 'NotInterested'


class stall(PeerMessage):
    # format: <len=0001><id=0>
    def __str__(self):
        return 'stall'


class Unstall(PeerMessage):
    # format: <len=0001><id=1>
    def __str__(self):
        return 'Unstall'


class Have(PeerMessage):
    def __init__(self, index: int):
        self.index = index

    def encode(self):
        return struct.pack('>IbI',
                           5,  # Message length
                           PeerMessage.Have,
                           self.index)

    @classmethod
    def decode(cls, data: bytes):
        logging.debug('Decoding Have of length: {length}'.format(
            length=len(data)))
        index = struct.unpack('>IbI', data)[2]
        return cls(index)

    def __str__(self):
        return 'Have'


class Request(PeerMessage):
    # requests a block (2^14 bytes) of a piece, excluding the final one.
    # format: <len=0013><id=6><index><begin><length>
    def __init__(self, index: int, begin: int, length: int = REQUEST_SIZE):
        # index: The zero based piece index
        # begin: The zero based offset within a piece
        # length: The requested length of data
        self.index = index
        self.begin = begin
        self.length = length

    def encode(self):
        return struct.pack('>IbIII',
                           13,
                           PeerMessage.Request,
                           self.index,
                           self.begin,
                           self.length)
   
    @classmethod
    def decode(cls, data: bytes):
        logging.debug('Decoding Request of length: {length}'.format(
            length=len(data)))

        parts = struct.unpack('>IbIII', data)
        return cls(parts[2], parts[3], parts[4])

    def __str__(self):
        return 'Request'


class Piece(PeerMessage):
    # format: <length prefix><message ID><index><begin><block>
    length = 9

    def __init__(self, index: int, begin: int, block: bytes):
        # index: The zero based piece index
        # begin: The zero based offset within a piece
        # block: The block data
        self.index = index
        self.begin = begin
        self.block = block

    def encode(self):
        message_length = Piece.length + len(self.block)
        return struct.pack('>IbII' + str(len(self.block)) + 's',
                           message_length,
                           PeerMessage.Piece,
                           self.index,
                           self.begin,
                           self.block)

    @classmethod
    def decode(cls, data: bytes):
        logging.debug('Decoding Piece of length: {length}'.format(
            length=len(data)))
        length = struct.unpack('>I', data[:4])[0]
        parts = struct.unpack('>IbII' + str(length - Piece.length) + 's',
                              data[:length+4])
        return cls(parts[2], parts[3], parts[4])

    def __str__(self):
        return 'Piece'


# Sends a message and cancels a previously requested block.
class Cancel(PeerMessage):
    # format: <len=0013><id=8><index><begin><length>
    def __init__(self, index, begin, length: int = REQUEST_SIZE):
        self.index = index
        self.begin = begin
        self.length = length

    def encode(self):
        return struct.pack('>IbIII',
                           13,
                           PeerMessage.Cancel,
                           self.index,
                           self.begin,
                           self.length)

    @classmethod
    def decode(cls, data: bytes):
        logging.debug('Decoding Cancel of length: {length}'.format(
            length=len(data)))
        parts = struct.unpack('>IbIII', data)
        return cls(parts[2], parts[3], parts[4])

    def __str__(self):
        return 'Cancel'