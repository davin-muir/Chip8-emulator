from collections import namedtuple, defaultdict
from asyncio import Queue
from hashlib import sha1

from client.protocol import PeerConnection, REQUEST_SIZE
from client.tracker import Tracker



# Maximum peer connections for each client.
MAX_PEER_CONNECTIONS = 40


'''
The torrent client is the native peer that initiates & stores peer-peer connections
to download or upload the pieces of a torrent file.

Once initiated, the client periodically calls the tracker, registered in the torrent's
meta-data. These trackers call for results in a list of peers to be hit in order to exchange pieces.

Each peer returned is stored in a queue to be consumed by a pool of PeerConnection objects.
'''
class TorrentClient:
    def __init__(self, torrent):
        self.tracker = Tracker(torrent)
        
        # A list of potential peers is the active queue, to be consumed by PeerConnections
        self.available_peers = Queue()
        
        # A list of workers may or may not be connected to a peer. Otherwise they're waiting to
        #  consume new remote peers from the available_peers queue.
        self.peers = []
        
        # The piece manager defines how peers make requests, and how they are propogated to the disk.
        self.piece_manager = PieceManager(torrent)
        self.abort = False
        
    
    
    """
    Starts dowloading the torrent held by this client. 
    It then connects to the tracker to retrieve a list of peers to communicate with,
    until the torrent is either fully downloaded or aborted.
    """
    async def start(self):
        self.peers = [PeerConnection(self.available_peers,
                                     self.tracker.torrent.info_hash,
                                     self.tracker.peer_id,
                                     self.piece_manager,
                                     self._on_block_retrieved)
                      for _ in range(MAX_PEER_CONNECTIONS)]

        # Timestamp for the previous call
        previous = None
        # Default interval between announcement calls (seconds)
        interval = 30*60

        while True:
            if self.piece_manager.complete:
                logging.info('Torrent fully downloaded!')
                break
            if self.abort:
                logging.info('Aborting download...')
                break

            current = time.time()
            if (not previous) or (previous + interval < current):
                response = await self.tracker.connect(
                    first=previous if previous else False,
                    uploaded=self.piece_manager.bytes_uploaded,
                    downloaded=self.piece_manager.bytes_downloaded)

                if response:
                    previous = current
                    interval = response.interval
                    self._empty_queue()
                    for peer in response.peers:
                        self.available_peers.put_nowait(peer)
            else:
                await asyncio.sleep(5)
        self.stop()

    def _empty_queue(self):
        while not self.available_peers.empty():
            self.available_peers.get_nowait()
    
    # Stops active downloads or seeding.
    def stop(self):
        self.abort = True
        for peer in self.peers:
            peer.stop()
        self.piece_manager.close()
        self.tracker.close()
    
    """
    Called by the PeerConnection when a peer retrieves a block.
    peer_id: The id of the retrieving peer.
    piece_index: The block's piece index.
    block_offset: The block's offset within the piece
    data: The retrieved data (binary)
    """
    def _on_block_retrieved(self, peer_id, piece_index, block_offset, data):
        self.piece_manager.block_received(
            peer_id=peer_id, piece_index=piece_index,
            block_offset=block_offset, data=data)


# Blocks are partial pieces that are transfered between peers.
class Block:
    Missing = 0
    Pending = 1
    Retrieved = 2

    def __init__(self, piece: int, offset: int, length: int):
        self.piece = piece
        self.offset = offset
        self.length = length
        self.status = Block.Missing
        self.data = None


class Piece:
    """
    Each torrent contains many pieces, which are equal in length, except for the final one, 
    which is usually shorter. When data is transfered between pieces, the smaller piece is refered to
    as a 'block.'
    """
    def __init__(self, index: int, blocks: [], hash_value):
        self.index = index
        self.blocks = blocks
        self.hash = hash_value

    # Resets all blocks regardless of their state.
    def reset(self):
        for block in self.blocks:
            block.status = Block.Missing

    # Retrieves the next block to be requested.
    def next_request(self) -> Block:
        missing = [b for b in self.blocks if b.status is Block.Missing]
        if missing:
            missing[0].status = Block.Pending
            return missing[0]
        return None
 
    # Updates block information recieved from the specified block.
    # offset: The block offset (within the piece)
    # data: The block data
    def block_received(self, offset: int, data: bytes):
        matches = [b for b in self.blocks if b.offset == offset]
        block = matches[0] if matches else None
        if block:
            block.status = Block.Retrieved
            block.data = data
        else:
            logging.warning('Attempting to complete non-existent block {offset}'
                            .format(offset=offset))
    
    # Checks if all blocks for the specified piece have been recieved, regardless of the SHA1 status.
    # returns True or False
    def is_complete(self) -> bool:
        blocks = [b for b in self.blocks if b.status is not Block.Retrieved]
        return len(blocks) is 0
   
    # Checks if a SHA1 hash for all recieved blocks matches the piece hash from the torrent's meta-data.
    # returns True or False
    def is_hash_matching(self):
        piece_hash = sha1(self.data).digest()
        return self.hash == piece_hash

    # Concatenates all blocks in the order and returns the data for the piece.
    @property
    def data(self):
        retrieved = sorted(self.blocks, key=lambda b: b.offset)
        blocks_data = [b.data for b in retrieved]
        return b''.join(blocks_data)

PendingRequest = namedtuple('PendingRequest', ['block', 'added'])

# The PieceManager keeps track of all the available pieces and how requests are made.
class PieceManager:
    def __init__(self, torrent):
        self.torrent = torrent
        self.peers = {}
        self.pending_blocks = []
        self.missing_pieces = []
        self.ongoing_pieces = []
        self.have_pieces = []
        self.max_pending_time = 300 * 1000  # 5 minutes
        self.missing_pieces = self._initiate_pieces()
        self.total_pieces = len(torrent.pieces)
        self.fd = os.open(self.torrent.output_file,  os.O_RDWR | os.O_CREAT)

    # Pre-constructs the list of pieces & blocks based on the amount of pieces & the size of the data being requested for the torrent.
    def _initiate_pieces(self) -> [Piece]:
        torrent = self.torrent
        pieces = []
        total_pieces = len(torrent.pieces)
        std_piece_blocks = math.ceil(torrent.piece_length / REQUEST_SIZE)
        
        # Uses the request size as a divisor for the piece length to calculate the number of active blocks.
        for index, hash_value in enumerate(torrent.pieces):
            if index < (total_pieces - 1):
                blocks = [Block(index, offset * REQUEST_SIZE, REQUEST_SIZE)
                          for offset in range(std_piece_blocks)]
            else:
                last_length = torrent.total_size % torrent.piece_length
                num_blocks = math.ceil(last_length / REQUEST_SIZE)
                blocks = [Block(index, offset * REQUEST_SIZE, REQUEST_SIZE)
                          for offset in range(num_blocks)]

                if last_length % REQUEST_SIZE > 0:
                    # The last block of the final piece could be smaller.
                    last_block = blocks[-1]
                    last_block.length = last_length % REQUEST_SIZE
                    blocks[-1] = last_block
            pieces.append(Piece(index, blocks, hash_value))
        return pieces

    # Closes any resources that were used by the PieceManager.
    def close(self):
        if self.fd:
            os.close(self.fd)
    
    # Checks if all the pieces for the torrent have been downloaded.
    # Returns True of False
    @property
    def complete(self):
        return len(self.have_pieces) == self.total_pieces
    
    # Gets the amount of bytes dwnloaded
    # Only counts complete, verified pieces, not single blocks.
    @property
    def bytes_downloaded(self) -> int:
        return len(self.have_pieces) * self.torrent.piece_length

    @property
    def bytes_uploaded(self) -> int:
        return 0
    
    # Adds a peer and a bitfield representing the pieces that a peer has.
    def add_peer(self, peer_id, bitfield):
        self.peers[peer_id] = bitfield

    # Updates the informatio nregarding the pieces stored by the current peer.
    def update_peer(self, peer_id, index: int):
        if peer_id in self.peers:
            self.peers[peer_id][index] = 1
 
    # Tries to remove the preceeding peer.
    def remove_peer(self, peer_id):
        if peer_id in self.peers:
            del self.peers[peer_id]
    
    # Gets the next block that should be requested from the given peer.
    # If there are none left to be retrieved, it returns None.
    def next_request(self, peer_id) -> Block:
        # The algorithm tries to download the pieces in sequence and tries to finish the started pieces before hitting new ones.

        # 1. Checks if any blocks are pending and need to make another request due to timeout.
        # 2. Checks the ongoing pieces to get the next block to be requested.
        # 3. Checks if this peer has any incomplete pieces.
        if peer_id not in self.peers:
            return None

        block = self._expired_requests(peer_id)
        if not block:
            block = self._next_ongoing(peer_id)
            if not block:
                block = self._get_rarest_piece(peer_id).next_request()
        return block
    
    # Called when a block has successfully been retrieved by a peer.
    # Once a full piece has been retrieved, it initializes a SHA1 hash control.
    # If the check fails, all the piece's blocks are set to 'missing' to be fetched again.
    def block_received(self, peer_id, piece_index, block_offset, data):
        logging.debug('Received block {block_offset} for piece {piece_index} '
                      'from peer {peer_id}: '.format(block_offset=block_offset,
                                                     piece_index=piece_index,
                                                     peer_id=peer_id))

        # Remove from pending requests
        for index, request in enumerate(self.pending_blocks):
            if request.block.piece == piece_index and \
               request.block.offset == block_offset:
                del self.pending_blocks[index]
                break

        pieces = [p for p in self.ongoing_pieces if p.index == piece_index]
        piece = pieces[0] if pieces else None
        if piece:
            piece.block_received(block_offset, data)
            if piece.is_complete():
                if piece.is_hash_matching():
                    self._write(piece)
                    self.ongoing_pieces.remove(piece)
                    self.have_pieces.append(piece)
                    complete = (self.total_pieces -
                                len(self.missing_pieces) -
                                len(self.ongoing_pieces))
                    logging.info(
                        '{complete} / {total} pieces downloaded {per:.3f} %'
                        .format(complete=complete,
                                total=self.total_pieces,
                                per=(complete/self.total_pieces)*100))
                else:
                    logging.info('Discarding corrupt piece {index}'
                                 .format(index=piece.index))
                    piece.reset()
        else:
            logging.warning('Trying to update piece that is not ongoing!')
   
   # Iterates over previously requested blocks, if any of they have been in the 'requested' state longer than the 'MAX_PENDING_TIME',
   #  the block is returned to be re-requested. Otherwise it returns None.
    def _expired_requests(self, peer_id) -> Block:
        current = int(round(time.time() * 1000))
        for request in self.pending_blocks:
            if self.peers[peer_id][request.block.piece]:
                if request.added + self.max_pending_time < current:
                    logging.info('Re-requesting block {block} for '
                                 'piece {piece}'.format(
                                    block=request.block.offset,
                                    piece=request.block.piece))
                    # Resets the expiration timer.
                    request.added = current
                    return request.block
        return None
   
    # Iterates over the ongoing pieces and returns the next block to be requested, or None if there are none left to be requested.
    def _next_ongoing(self, peer_id) -> Block:
        for piece in self.ongoing_pieces:
            if self.peers[peer_id][piece.index]:
                # So you uh, got any blocks left?
                block = piece.next_request()
                if block:
                    self.pending_blocks.append(
                        PendingRequest(block, int(round(time.time() * 1000))))
                    return block
        return None
    
    # Given the current list of missing pieces, it gets the piece with the least repititions amongs the bundle of peers.
    def _get_rarest_piece(self, peer_id):
        piece_count = defaultdict(int)
        for piece in self.missing_pieces:
            if not self.peers[peer_id][piece.index]:
                continue
            for p in self.peers:
                if self.peers[p][piece.index]:
                    piece_count[piece] += 1

        rarest_piece = min(piece_count, key=lambda p: piece_count[p])
        self.missing_pieces.remove(rarest_piece)
        self.ongoing_pieces.append(rarest_piece)
        return rarest_piece
   
    # Iterates over the missing pieces and returns the next block to be requested, or None if there are none left.
    # This changes the state of the piece from missing to ongoing, and so the next time this function is called it will move on to the next missing piece in the sequence.
    def _next_missing(self, peer_id) -> Block:
        for index, piece in enumerate(self.missing_pieces):
            if self.peers[peer_id][piece.index]:
    
                piece = self.missing_pieces.pop(index)
                self.ongoing_pieces.append(piece)

                return piece.next_request()
        return None
   
   # Writes the specified piece to the disk.
    def _write(self, piece):
        pos = piece.index * self.torrent.piece_length
        os.lseek(self.fd, pos, os.SEEK_SET)
        os.write(self.fd, piece.data)