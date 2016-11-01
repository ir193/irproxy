from __future__ import print_function
import logging

try:
    from io import BytesIO
except ImportError:
    from StringIO import StringIO as BytesIO

HTTP_REQUEST, HTTP_RESPONSE, HTTP_BOTH = 0, 1, 2

HTTP_EVENTS = ['cb_on_message_begin',

        'cb_on_line_done',
        'cb_on_header_done',
        'cb_on_flush_header',
        'cb_on_body_done',
        'cb_on_flush_body',
        'cb_on_message_done'
        ]

_state = ['',
    's_dead',

    's_start_req_or_res',
    's_start_res',

    's_res_line',

    's_start_req',
    's_req_line',

    's_header_field_start',
    's_headers_done',

    's_data_with_length',
    's_chunk_data',
    's_connect_data',

    's_message_done'
]

class Enum(tuple): __getattr__ = tuple.index

PARSER_STATE = Enum(_state)


class ParseError(Exception):
    pass

class NeedMoreError(Exception):
    pass

CR, LF = b'\r', b'\n'

def check_line(f):
    def deco(self):
        if self.first_line_end == -1:
            self.parsing = False
            return
        f(self)
    return deco

class HTTPParser():

    def __init__(self, type = HTTP_BOTH, data = b'', setting={}):
        self.buf = data

        self.type = type
        self.setting = {}
        self.parsing = True

        self.headers = {}
        self.body = b''

        if type==HTTP_REQUEST:
            self.state = PARSER_STATE.s_req_line
        elif type==HTTP_RESPONSE:
            self.state = PARSER_STATE.s_res_line
        elif type==HTTP_BOTH:
            self.state = PARSER_STATE.s_start_req_or_res

        for ev, cb in setting.items():
            if ev in HTTP_EVENTS:
                self.setting[ev] = cb

    def flush(self, data):
        if len(data) == 0:
            if self.state == PARSER_STATE.s_message_done:
                #print('done')
                return
            elif self.state in [PARSER_STATE.s_dead, PARSER_STATE.s_res_line,
                PARSER_STATE.s_req_line, PARSER_STATE.s_start_req_or_res]:
                return 
            else:
                print('now ', 'on'+_state[self.state][1:])
                raise ParseError("Connect closed with partial data")

        self.parsing = True

        self.buf += data

        #print('parse [%s]' %self.buf)
        c = 0
        while True:
            name = 'on'+_state[self.state][1:]
            #print(c, 'now ', name)
            c += 1
            if not self.parsing:
                break

            handler = getattr(self,name)
            try:
                handler()
            except NeedMoreError:
                break
        #print(s, handler)

    def on_dead(self):
        raise ParseError('Flush new data on dead state')

    def on_start_req_or_res(self):
        guess = self._guess(b'HTTP/')
        if guess == -1:
            self.type = HTTP_REQUEST
            self.state = PARSER_STATE.s_req_line
        elif guess == 5:
            self.type = HTTP_RESPONSE
            self.state = PARSER_STATE.s_res_line
        else:
            # need more data
            raise NeedMoreError

    def on_res_line(self):
        line = self._readline()
        try:
            unpack = line.decode('ascii').split(' ')
            self.version = unpack[0].capitalize()
            self.status_code = unpack[1]
            self.reason = unpack[2]
        except :
            raise ParseError('Error method format')

        self.state = PARSER_STATE.s_header_field_start
        if 'cb_on_line_done' in self.setting:
            logging.debug('callback line done')
            self.setting['cb_on_line_done']()

    def on_req_line(self):
        line = self._readline()
        try:
            unpack = line.decode('ascii').split(' ')
            self.method = unpack[0].upper()
            self.url = unpack[1]
            self.version = unpack[2]
        except :
            raise ParseError('Error method format')

        self.state = PARSER_STATE.s_header_field_start
        if 'cb_on_line_done' in self.setting:
            logging.debug('callback line done')
            self.setting['cb_on_line_done']()

    def on_header_field_start(self):
        if 'cb_on_flush_header' in self.setting:
            logging.debug('callback flush header')
            self.setting['on_flush_header']()

        line = self._readline()
        if line == b'\r\n':
            self.state = PARSER_STATE.s_headers_done
            return

        try:
            sep = line.find(b':')
            key = line[:sep].decode('ascii').lower()
            value = line[sep+1:-2].decode('ascii')

        except:
            raise ParseError('Error Header')

        self.headers[key] = value

    def on_headers_done(self):
        if 'cb_on_header_done' in self.setting:
            logging.debug('callback header done')
            self.setting['cb_on_header_done']()

        if self.type == HTTP_REQUEST and self.method == 'CONNECT':
            self.state = PARSER_STATE.s_connect_data
            return

        if self.type == HTTP_REQUEST and self.method != "POST":
            self.state = PARSER_STATE.s_message_done
            self.parsing = False
            return

        if 'content-length' in self.headers:
            self.state = PARSER_STATE.s_data_with_length
            return
        elif ('transfer-encoding' in self.headers
             and self.headers['transfer-encoding'][1].lower() == 'chunked'):
            self.state = PARSER_STATE.s_chunk_data_begin

    def on_connect_data(self):
        if not self.buf:
            self.parsing = False
            return
        self.new_body = self.buf
        #print('buf', self.buf)
        self.body += self.buf   
        self.buf = b''
        self.parsing = False
        if 'cb_on_flush_body' in self.setting:
            self.setting['cb_on_flush_body']()

    def on_data_with_length(self):
        length = int(self.headers['content-length'])
        
        remain = length - len(self.body)
        #print('body with len', length, remain, 'to read')

        self.body += self.buf[:remain]
        self.new_body = self.buf[:remain]
        self.buf = self.buf[remain:]

        remain = length - len(self.body)

        if 'cb_on_flush_body' in self.setting:
            logging.debug('callback flush body')
            self.setting['cb_on_flush_body']()

        if remain == 0:
            print('finish')
            self.state = PARSER_STATE.s_message_done
            return
        else:
            self.parsing = False



    def on_chunk_data_begin(self):
        while True:
            line = self._readline()
            if line != b'\r\n':
                break
        self.chunk_size = int(line, 16)
        if self.chunk_size == 0:
            self.state =PARSER_STATE.s_chunk_data_end
            return
        self.chunk = b''
        self.state = PARSER_STATE.s_chunk_data_more

    def on_chunk_data_more(self):
        remain = self.chunk_size - len(self.chunk)
        self.chunk += self.buf[:remain]
        self.new_body = self.buf[:remain]
        self.buf = self.buf[remain:]
        self.body += self.chunk

        if 'cb_on_flush_body' in self.setting:
            self.setting['cb_on_flush_body']()

        if len(self.chunk) == self.chunk_size:
            self.state = PARSER_STATE.s_chunk_data_begin

    def on_start_res(self):
        self.parsing = False

    def on_message_done(self):
        self.parsing = False
        if 'cb_on_message_done' in self.setting:
            self.setting['cb_on_message_done']()
        return


    def _readline(self):
        pos = self.buf.find(CR+LF)
        if pos == -1:
            raise NeedMoreError('No line to read')

        line = self.buf[:pos+2]
        self.buf = self.buf[pos+2:]
        return line

    def _guess(self, s):
        if len(s) > len(self.buf):
            if s.startswith(self.buf):
                return len(self.buf)
            else:
                return -1
        else:
            if self.buf.startswith(s):

                return len(s)
            else:
                return -1
