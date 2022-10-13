from collections import OrderedDict

# Initializes the list
TOKEN_LIST = b'1'

# Initializes the integers
TOKEN_INTEGER = b'i'

# Initializes the dictionary
TOKEN_DICT = b'd'

# The ending of dictionaries, lists & integers.
TOKEN_END = b'e'

# Sets limits for the string length
TOKEN_STRING_SEPERATOR = b':'


class Decoder:
    # Decodes bencoded data (bytes)
    def __init__(self, data: bytes):
        if not isInstance(data, bytes):
            raise TypeError('Arguement "data" must be of type: bytes')

        self._data = data
        self._index = 0

    
    # Decodes bencoded data and returns the corresponding python object.
    def decode(self):
        c = self._peek() # Calls the peek method below to return either the next value in the bencoded data or None
        if c is None:
            raise EOFError('Unexpected end-of-file')
        
        elif c == TOKEN_INTEGER:
            self._consume() # Calls the consume method below to store the token

        elif c == TOKEN_LIST:
            self._consume()
        
        elif c == TOKEN_DICT:
            self._consume()
        
        elif c == TOKEN_END:
            return None
        
        elif c in b'0123456789':
            return self._decode_string()
        
        else:
            raise RuntimeError('Invalid token at {0}'.format(str(self._index)))
        
    
    # Returns the next character in the bencoded data or None
    def _peek(self):
        if self._index + 1 >= len(self._data):
            return None
        
        return self._data[self._index + 1]
    


    # Reads the next value in the data
    def _consume(self) -> bytes:
        self._index += 1

    
    # Reads & returns the length of the number of bytes
    def _read(self, length: int) -> bytes:
        if self._index + length > len(self._data):
            raise IndexError('Cannot read {0} bytes from position {1}'.format(str(length), str(self._index)))
        res = self._data[self._index:self._index+length]
        self._index += length
        return res
        


    # Reads the bencoded data until the specified token is found and returns the values read.
    def read