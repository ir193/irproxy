from __future__ import print_function

import asyncore
import asynchat
import logging
import socket
from parse_http import *

try:
    from urllib.parse import urlparse, urlunparse
except ImportError:
    from urlparse import urlparse, urlunparse

class BufferedDispatcher(asyncore.dispatcher):
    def __init__(self, sock=None, buf_size=1024):
        asyncore.dispatcher.__init__(self, sock)
        self.out_buffer = []
        self.buf_size = buf_size

    def initiate_send(self):
        num_sent = 0
        while self.out_buffer and self.connected:
            data = self.out_buffer.pop(0)
            if data == None:
                self.handle_close()
                return

            #print('sending', data)
            num_sent = asyncore.dispatcher.send(self, data)
            #print(data)
            if num_sent != len(data):
                data = data[num_sent:]
                self.out_buffer.insert(0, data)

    def handle_write(self):
        self.initiate_send()

    def writable(self):
        return (not self.connected) or len(self.out_buffer)

    def send(self, data):
        for i in range(0, len(data), self.buf_size):
            self.out_buffer.append(data[i:i+self.buf_size])

        self.initiate_send()

    def close_when_done(self):
        self.out_buffer.append(None)


class HTTPClient(BufferedDispatcher):
    def __init__(self, server, host, port, path):
        BufferedDispatcher.__init__(self)
        self.host = host
        self.port = port
        self.path = path
        self.server = server

        #self.parser = HTTPParser(type=HTTP_REQUEST, setting=setting)

        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            logging.info("{0}{1}".format(self.host, self.path))
            self.connect((self.host, self.port))
        except socket.error as e:
            self.server.sender_error(e)
            self.close()
            return
        
    def handle_connect(self):
        try:
            logging.debug('client connect')
            self.server.establish_tunnel()
        except socket.error as e:
            logging.error("Error establish_tunnel")
            self.server.sender_error(e)
            self.close()
            return

    def send(self, data):
        #print('client send', data)
        BufferedDispatcher.send(self, data)


    def handle_read(self):
        data = self.recv(4096)
        self.server.send(data)

    def handle_close(self):
        logging.debug('close connection to {0}'.format(self.host))
        if hasattr(self, 'client'): 
            self.server.close_when_done()
            del self.server
        self.close()

# asynchat.async_chat
class HTTPServer(BufferedDispatcher):
    def __init__(self, sock, addr, port):
        BufferedDispatcher.__init__(self, sock=sock)
        setting = {
            'cb_on_line_done' : self.handle_line_done,
            'cb_on_header_done' : self.handle_header_done,
            'cb_on_flush_body' : self.handle_new_data
        }

        self.request = b''
        self.parser = HTTPParser(type=HTTP_REQUEST, setting=setting)
        self.established = False

    def handle_line_done(self):
        self.method, self.url = self.parser.method, self.parser.url

        if self.method == 'CONNECT': 
            self.netloc = self.url
            self.scheme = 'https'
            self.path = ''
            params, query, fragment = '', '', ''
        else:
            (self.scheme, self.netloc, self.path, 
             params, query, fragment) = urlparse(self.url)

        if ':' in self.netloc:
            self.target_host, self.target_port = self.netloc.split(':')
            self.target_port = int(self.target_port)
        else:
            self.target_host = self.netloc
            if self.method == 'CONNECT': self.target_port = 443  # default SSL port
            else: self.target_port = 80

        self.target_path = urlunparse(('', '', 
                                    self.path, params, query, fragment))

        self.client = HTTPClient(self,
                                 self.target_host,
                                 self.target_port,
                                 self.target_path,
                                 )

    def handle_header_done(self):
        if self.method == 'CONNECT':
            #self.request = 
            pass
        else:
            self.headers = self.parser.headers
            for i in ['proxy-connection', 'connection', 'keep-alive']:
                if i in self.headers:
                    del self.headers[i]
            
            self.headers['Connection'] = 'close'

            r = []
            r.append('{0} {1} HTTP/1.1'.format(self.method, 
                                          self.target_path))

            for k, v in self.parser.headers.items():
                r.append('{0}: {1}'.format(k.capitalize(), v))
            r.append('\r\n')

            self.request = '\r\n'.join(r).encode('ascii')

            self.client.send(self.request)
        #self.client.send(request.encode('ascii'))

    def handle_new_data(self):
        #print('new data', self.parser.new_body)
        self.client.send(self.parser.new_body)

    def establish_tunnel(self):
        if self.method == 'CONNECT':
            self.send(b'HTTP/1.1 200 Connection established\r\nProxy-agent: test-proxy\r\n\r\n')


    def sender_error(self, e):
        raise e

    def handle_read(self):
        try:
            data = self.recv(4096)
            #print('!!!', data)
            status = self.parser.flush(data)
            #print(_state[self.state])

        except socket.error as why:
            self.handle_error()
            return

    def handle_close(self):
        if hasattr(self, 'client'): 
            # self.sender.close() should be fine except for PUT requests?
            self.client.close_when_done()
            del self.client # break circular reference
        self.close()

class Dispatcher(asyncore.dispatcher):
    def __init__(self, host, port):
        asyncore.dispatcher.__init__(self)
        self.port = port
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        logging.info("listening on {0}:{1}".format(host, port))
        self.bind(("", port))
        self.listen(5)

    def handle_accept(self):
        conn, addr = self.accept()
        #logging.info("Incoming connection from:{0}".format(addr[0]))
        HTTPServer(conn, *addr)


HOST = ''
PORT = 8000
if __name__ == '__main__':
    logging.basicConfig(format='%(message)s', level=logging.INFO)
    logging.debug('start')

    s = Dispatcher(HOST, PORT)
    try:
        asyncore.loop(timeout=1)
    except Exception as e:
        raise e