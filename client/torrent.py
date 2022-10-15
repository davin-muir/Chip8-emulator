from hashlib import sha1
from collections import namedtuple

from . import bencode


TorrentFile = namedtuple('TorrentFile', ['name', 'length'])


# A representation of the torrent meta-data kept within a torrent file.
class Torrent:
    def __init__(self, filename):
        self.filename = filename
        self.files = []

        with open(self.filename, 'rb') as f:
            meta_info = f.read()
            self.meta_info = bencoding.Decoder(meta_info).decode()
            info = bencoding.Encoder(self.meta_info[b'info']).encode()
            self.info_hash = sha1(info).digest()
            self._identify_files()

    # Identifies the files included in the torrent.
    def _identify_files(self):
        if self.multi_file:
            raise RuntimeError('Multi-file torrents is not supported!')
        self.files.append(
            TorrentFile(
                self.meta_info[b'info'][b'name'].decode('utf-8'),
                self.meta_info[b'info'][b'length']))

    # The announcement URL to the tracker.
    @property
    def announce(self) -> str:
        return self.meta_info[b'announce'].decode('utf-8')

    # Does the torrent have multiple files?
    @property
    def multi_file(self) -> bool:
        return b'files' in self.meta_info[b'info']
    
    # Gets the length (bytes) for each piece.
    @property
    def piece_length(self) -> int:
        return self.meta_info[b'info'][b'piece length']

    # The total size (bytes) of all the files in the torrent.
    # Returns the total size of the torrent's data.
    @property
    def total_size(self) -> int:
        if self.multi_file:
            raise RuntimeError('Multi-file torrents is not supported!')
        return self.files[0].length

    @property
    def pieces(self):
        data = self.meta_info[b'info'][b'pieces']
        pieces = []
        offset = 0
        length = len(data)

        while offset < length:
            pieces.append(data[offset:offset + 20])
            offset += 20
        return pieces

    @property
    def output_file(self):
        return self.meta_info[b'info'][b'name'].decode('utf-8')

    def __str__(self):
        return 'Filename: {0}\n' \
               'File length: {1}\n' \
               'Announce URL: {2}\n' \
               'Hash: {3}'.format(self.meta_info[b'info'][b'name'],
                                  self.meta_info[b'info'][b'length'],
                                  self.meta_info[b'announce'],
                                  self.info_hash)