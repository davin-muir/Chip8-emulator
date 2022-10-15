import aiohttp, logging, random, socket
from urllib.parse import urlencode
from struct import unpack

from . import bencode


# The tracker's response after a succesful connection to the announcement URL.
class TrackerResponse:
    def __init__(self, response: dict):
        self.response = response

    @property
    def failure(self):
        if b'failure reason' in self.response:
            return self.response[b'failure reason'].decode('utf-8')
        return None
        
    # Interval (seconds) between requests made to the tracker from the client.
    @property
    def interval(self) -> int:
        return self.response.get(b'interval', 0)
    
    # The total amount of peers in the file.
    @property
    def complete(self) -> int:
        return self.response.get(b'complete', 0)

    @property
    def incomplete(self) -> int:
        return self.response.get(b'incomplete', 0)
    
    # A list of peers structured as a tuple (ip, port)
    @property
    def peers(self):
        peers = self.response[b'peers']
        if type(peers) == list:
            logging.debug('Dictionary model peers are returned by tracker')
            raise NotImplementedError()
        else:
            logging.debug('Binary model peers have been returned by the tracker')

            # Splits the string in 6 byte long pieces, where the first
            #  4 characters is the IP the last 2 is the TCP port.
            peers = [peers[i:i+6] for i in range(0, len(peers), 6)]

            # Converts the encoded address to a list of tuples
            return [(socket.inet_ntoa(p[:4]), _decode_port(p[4:]))
                    for p in peers]

    def __str__(self):
        return "incomplete: {incomplete}\n" \
               "complete: {complete}\n" \
               "interval: {interval}\n" \
               "peers: {peers}\n".format(
                   incomplete=self.incomplete,
                   complete=self.complete,
                   interval=self.interval,
                   peers=", ".join([x for (x, _) in self.peers]))


class Tracker:
    def __init__(self, torrent):
        self.torrent = torrent
        self.peer_id = _calculate_peer_id()
        self.http_client = aiohttp.ClientSession()

    async def connect(self,
                      first: bool = None,
                      uploaded: int = 0,
                      downloaded: int = 0):
        """
        
        # first: is this the first announcement call or not?
        # uploaded: The amountof bytes uploaded
        # downloaded: The amount of bytes downloaded
        """
        params = {
            'info_hash': self.torrent.info_hash,
            'peer_id': self.peer_id,
            'port': 6889,
            'uploaded': uploaded,
            'downloaded': downloaded,
            'left': self.torrent.total_size - downloaded,
            'compact': 1}
        if first:
            params['event'] = 'started'

        url = self.torrent.announce + '?' + urlencode(params)
        logging.info('Connecting to tracker at: ' + url)

        async with self.http_client.get(url) as response:
            if not response.status == 200:
                raise ConnectionError('Unable to connect to tracker: status code {0}'.format(response.status))
            data = await response.read()
            self.raise_for_error(data)
            return TrackerResponse(bencoding.Decoder(data).decode())

    def close(self):
        self.http_client.close()

    
    # Constructs the URL parameters used when issuing the announcment.
    def _construct_tracker_parameters(self):
        return {
            'info_hash': self.torrent.info_hash,
            'peer_id': self.peer_id,
            'port': 6889,
            # TODO Update stats when communicating with tracker
            'uploaded': 0,
            'downloaded': 0,
            'left': 0,
            'compact': 1}


def _calculate_peer_id():
    return '-PC0001-' + ''.join(
        [str(random.randint(0, 9)) for _ in range(12)])


# Converts a 32-bit packed binary port to an integer.
def _decode_port(port):
    return unpack(">H", port)[0]