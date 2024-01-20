#!/usr/bin/env python3
# import sys;sys.path.append(r'~/.p2/pool/plugins/org.python.pydev.core_10.2.1.202307021217/pysrc')
# import pydevd; pydevd.settrace()

import sys
import logging
import asyncio
import yaml

from time import sleep
from io import StringIO

from nio import AsyncClient, MatrixRoom, RoomMessageText, RoomMessageImage, LoginResponse

from asyncirc.protocol import IrcProtocol
from asyncirc.server import Server

from irclib.parser import Message
from urllib.parse import urlparse

loop = asyncio.get_event_loop()
loop.set_debug(False)


FORMAT = "[%(asctime)s] [%(name)s] [%(levelname)s]  %(message)s (%(filename)s:%(lineno)d)"

#logging.basicConfig(filename='relay.log', level=logging.DEBUG, format=FORMAT)
logging.basicConfig(level=logging.DEBUG, format=FORMAT)

logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("async-irc").setLevel(logging.WARNING)
logging.getLogger("nio").setLevel(logging.WARNING)


class Relay:
    def __init__(self,
                 matrix_host, matrix_domain, matrix_name, matrix_user, matrix_pwd,
                 irc_host, irc_port, irc_sasl, irc_user, irc_pwd,
                 relayed_rooms, log):
        self.log = log
        self.matrix_host = matrix_host
        self.matrix_domain = matrix_domain
        self.matrix_name = matrix_name
        self.matrix_user = matrix_user
        self.matrix_pwd = matrix_pwd
        
        self.irc_host = irc_host
        self.irc_port = irc_port
        self.irc_sasl = irc_sasl
        self.irc_user = irc_user
        self.irc_pwd = irc_pwd
        
        self.relayed_rooms = relayed_rooms
        
        self.accepted_irc = False
        self.accepted_matrix = False
        
    async def initialize(self):
        
        self.servers = [
            Server(self.irc_host, self.irc_port, self.irc_sasl, self.irc_pwd)
        ]
        
        self.matrix_client = AsyncClient(self.matrix_host, self.matrix_user)
        self.matrix_client.add_event_callback(self.matrix_msg_handler, RoomMessageText)
        self.matrix_client.add_event_callback(self.matrix_img_handler, RoomMessageImage)
        
        resp = await self.matrix_client.login(self.matrix_pwd, device_name="RelayHost")
        
        # check that we logged in successfully
        if isinstance(resp, LoginResponse):
            self.log.debug("Matrix: logged in")
            self.accepted_matrix = True

        else:
            self.log.debug(f'homeserver = "{homeserver}"; user = "{user_id}"')
            self.log.debug(f"Failed to log in: {resp}")

            sys.exit(1)
        
        self.irc_conn = IrcProtocol(self.servers, self.irc_user, loop=loop)
        self.irc_conn.register_cap('userhost-in-names')
        self.irc_conn.register('*', self.irc_msg_handler)  
        
        await self.irc_conn.connect()
                
        # If you made a new room and haven't joined as that user, you can use
        for _, room in self.relayed_rooms.items():
            room_enabled = room["enabled"]
            if room_enabled:
                await self.matrix_client.join(room["matrix_room"])
            
        await self.matrix_client.sync_forever(timeout=30000)  # milliseconds
        
    async def irc_msg_handler(self, conn: 'IrcProtocol', message: 'Message'):
    
        if not self.accepted_matrix:
            return
        
        if message.command == "NOTICE":
            for params in range(len(message.parameters)):
                self.log.debug(f"NOTICE: {params}")
                
        elif message.command == "PRIVMSG":
            # self.log.debug(message.command, len(message.parameters))
            message_sender = message.prefix.ident
            sender_alias = message.prefix.nick
            if message_sender == self.irc_user:
                return
            
            irc_room = message.parameters[0]
            message_body = message.parameters[1]
            
            for room_name, room_data in self.relayed_rooms.items():
                if irc_room == room_data["irc_room"]:
                    room_enabled = room_data["enabled"]
                    if room_enabled:
                        matrix_room = room_data['matrix_room']
                        matrix_room_id = room_data["matrix_room_id"]
                        self.log.debug(f"room bridged to {matrix_room}")
                        
                        await self.matrix_client.room_send(room_id=matrix_room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": f"<{sender_alias}> {message_body}"},
                        )
        
        elif message.command == "900":
            self.accepted_irc = True
            for _, room in self.relayed_rooms.items():                
                room_enabled = room["enabled"]
                if room_enabled:
                    msg = f"JOIN {room['irc_room']}"
                    self.log.debug(msg)
                    conn.send(msg)
        else:
            self.log.debug(f"{message.command} {len(message.parameters)}")
            for params in range(len(message.parameters)):
                self.log.debug(f"{params}")
    
    
    async def matrix_msg_handler(self, room: 'MatrixRoom', event: 'RoomMessageText') -> None:
        # self.log.debug(
        #     f"Message received in room {room.display_name} {room.room_id}\n"
        #     f"{room.user_name(event.sender)} | {event.body}"
        # )
        

        if not self.accepted_irc:
            return
        
        await self.matrix_client.update_receipt_marker(room.room_id, event.event_id)
        
        message_sender = room.user_name(event.sender)
        
        if message_sender == self.matrix_name:
            return
        
        matrix_room = room.room_id
        message_body = event.body
        
        for room_name, room_data in self.relayed_rooms.items():
            # self.log.debug(room_name, room_data)
            if matrix_room == room_data["matrix_room_id"]:
                room_enabled = room_data['enabled']
                if room_enabled:
                    irc_room = room_data['irc_room']
                    
                    # while message_body:
                    # text = message_body[:400]
                    # message_body = message_body[400:]
                    
                    for line_no, line in enumerate(StringIO(message_body)):
                        if line_no % 5 == 0:
                            sleep(5)
                            
                        while line:
                            msg = f"PRIVMSG {irc_room} :<{message_sender}> {line[:200]}"
                            line = line[200:]
                            # print(msg)
                            self.irc_conn.send_command(msg)
                        
        
    async def matrix_img_handler(self, room: 'MatrixRoom', event: 'RoomMessageImage') -> None:

        if not self.accepted_irc:
            return

        
        await self.matrix_client.update_receipt_marker(room.room_id, event.event_id)
        
        message_sender = room.user_name(event.sender)
        
        if message_sender == self.matrix_name:
            return
        
        matrix_room = room.room_id
        mxc_url = event.url
        
        o = urlparse(mxc_url)
        domain = o.netloc
        pic_code = o.path
        url = "https://{0}/_matrix/media/v1/download/{0}{1}".format(domain, pic_code)
        self.log.debug(url)
        
        for room_name, room_data in self.relayed_rooms.items():
            # self.log.debug(room_name, room_data)
            if matrix_room == room_data["matrix_room_id"]:
                room_enabled = room_data['enabled']
                if room_enabled:
                    irc_room = room_data['irc_room']
                    
                    while url:
                        msg = f"PRIVMSG {irc_room} :<{message_sender}> {url[:400]}"
                        url = url[400:]
                        self.irc_conn.send_command(msg)

async def main(argv) -> None:
    
    
    if len(argv) > 1:
        config_path = argv[1]
    else:
        print("usage: python3 relay.py config.yaml")
        sys.exit(1)

    log = logging.getLogger("relay")
    
    with open(config_path, 'r') as yml_file:
        cfg = yaml.load(yml_file, yaml.SafeLoader)
        
    if cfg["general"]["debug"] < 1:
        log.setLevel(logging.INFO)
    
    matrix_host = cfg["matrix"]["host"]
    matrix_domain = cfg["matrix"]["domain"]
    matrix_name = cfg["matrix"]["name"]
    matrix_user = cfg["matrix"]["user"]
    matrix_pwd = cfg["matrix"]["pwd"]
    
    irc_host = cfg["irc"]["host"]
    irc_port = cfg["irc"]["port"]
    irc_sasl = cfg["irc"]["sasl"]
    irc_user = cfg["irc"]["user"]
    irc_pwd = cfg["irc"]["pwd"]
    
    relayed_rooms = cfg["rooms"]
    
    relay = Relay(matrix_host, matrix_domain, matrix_name, matrix_user, matrix_pwd,
                  irc_host, irc_port, irc_sasl, irc_user, irc_pwd,
                  relayed_rooms, log)
    
    await relay.initialize()

if __name__ == "__main__":
    try:
        loop.run_until_complete(main(sys.argv))
    finally:
        loop.stop()
