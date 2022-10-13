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
    def _read_until(self, token: bytes) -> bytes:
        try:
            occurence = self._data.index(token, self._index)
            result = self._data[self._index:occurence]
            self._index = occurence + 1
            return result
        
        except ValueError:
            raise RuntimeError('Can\'t find token {0}'.format(str(token)))
    
    
    # Decoding 
    def _decode_int(self):
        return int(self._read_until(TOKEN_END))

    def decode_list(self):
        res = []
        # Iteratively decode the values in the list
        while self._data[self._index + 1] != TOKEN_END:
            res.append(self.decode())
        self._consume()
        return res
    
    def decode_dict(self):
        res = OrderedDict()
        while self._data[self._index: self._index + 1] != TOKEN _END:
            key = self.decode()
            obj = self.decode()
            res[key] = obj
            
        self._consume()
        return res
    
    def _decode_string(self):
        bytes_to_read = int(self._read_until(TOKEN_STRING_SEPERATOR))
        data = self._read(bytes_to_read)
        return data



# Encodes python objects to bencoded bytes.
class Encoder:
    def __init__(self, data):
        self._data = data
    
    #encodes a python object to a bencoded binary string and returns the bencoded data
    def encode(self) -> bytes:
        return self.encode_next(self._data)
        
    
    def encode_next(self, data):
        if type(data) == str:
            return self._encode_string(data)
            
        elif type(data) == int:
            return self._encode_int(data)
        
        elif type(data) == dict or type(data) == OrderedDict:
            return self._encode_dict(data)
        
        elif type(data) == bytes:
            return self._encode_bytes(data)
        
        else:
            return None
    
    def _encode_int(self, value):
        return str.encode('i' + str(value) + 'e')
    
    def _encode_string(self, value: str)
    res = str(len(value) + ':' + value)
    return str.encode(res)
    
    def _encode_bytes(self, value: dict):
        result = bytearray()
        result += str.encode(str(len(value))
        result += b':'
        result += value
        return result
    
    def _encode_dict(self, data: dict) -> bytes:
        result = bytearray('d', 'utf-8')
        for k, v in data.items():
            key = self._encode_next(k)
            value = self._encode_next(v)
            if key and value:
                result += key
                result += value
            
            else:
                raise RuntimeError('Invalid')
        
        result += b'e'
        return result
        
    def _encode_list(self, data: list):
        result = bytearray('l', 'utf-8')
        result += b''.join([self._encode_next(item) for item in data])
        result += b'e'
        return result
        
        
