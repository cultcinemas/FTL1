import asyncio
import logging

from pyrogram import Client

from . import multi_clients, work_loads, StreamBot
from ..vars import Var


async def initialize_clients():
    multi_clients[0] = StreamBot
    work_loads[0] = 0
    all_tokens = Var.MULTI_TOKENS.split()
    if not all_tokens:
        print("No additional clients found, using default client")
        return

    async def start_client(client_id, token):
        try:
            print(f"Starting - Client {client_id}")
            if client_id == len(all_tokens):
                await asyncio.sleep(2)
                print("This will take some time, please wait...")
            
            # Stagger client startup to dodge FloodWait
            await asyncio.sleep(client_id * 1.5)
            
            client = await Client(
                name=str(client_id),
                api_id=Var.API_ID,
                api_hash=Var.API_HASH,
                bot_token=token,
                sleep_threshold=Var.SLEEP_THRESHOLD,
                no_updates=True,
                in_memory=True
            ).start()
            work_loads[client_id] = 0
            multi_clients[client_id] = client
        except Exception:
            logging.error(f"Failed starting Client - {client_id} Error:", exc_info=True)

    await asyncio.gather(*[start_client(i, token.strip()) for i, token in enumerate(all_tokens, 1)])

    if len(multi_clients) != 1:
        Var.MULTI_CLIENT = True
        print("Multi-Client Mode Enabled")
    else:
        print("No additional clients were initialized, using default client")
        
