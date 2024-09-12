import asyncio
import os
import random
import aiofiles
from urllib.parse import unquote
import uuid

import aiohttp
import io
import mimetypes
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from datetime import datetime, timezone, timedelta
from pyrogram import Client
from pyrogram.errors import Unauthorized, UserDeactivated, AuthKeyUnregistered, FloodWait
from pyrogram.raw.functions.messages import RequestAppWebView
from pyrogram.raw.functions import account
from pyrogram.raw.types import InputBotAppShortName, InputNotifyPeer, InputPeerNotifySettings
from .agents import generate_random_user_agent
from bot.config import settings
from typing import Callable
import functools
from bot.utils import logger
from bot.exceptions import InvalidSession
from .headers import headers


def error_handler(func: Callable):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            await asyncio.sleep(1)
    return wrapper

class Tapper:
    def __init__(self, tg_client: Client, proxy: str):
        self.tg_client = tg_client
        self.session_name = tg_client.name
        self.proxy = proxy
        self.tg_web_data = None
        self.tg_client_id = 0

    async def get_tg_web_data(self) -> str:
        
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            proxy_dict = dict(
                scheme=proxy.protocol,
                hostname=proxy.host,
                port=proxy.port,
                username=proxy.login,
                password=proxy.password
            )
        else:
            proxy_dict = None

        self.tg_client.proxy = proxy_dict

        try:
            if not self.tg_client.is_connected:
                try:
                    await self.tg_client.connect()

                except (Unauthorized, UserDeactivated, AuthKeyUnregistered):
                    raise InvalidSession(self.session_name)
            
            while True:
                try:
                    peer = await self.tg_client.resolve_peer('catsgang_bot')
                    break
                except FloodWait as fl:
                    fls = fl.value

                    logger.warning(f"{self.session_name} | FloodWait {fl}")
                    logger.info(f"{self.session_name} | Sleep {fls}s")
                    await asyncio.sleep(fls + 3)
            
            ref_id = random.choices([settings.REF_ID, "BBbpkhoDpCz4-1wY-ZHVs"], weights=[85, 15], k=1)[0]
            web_view = await self.tg_client.invoke(RequestAppWebView(
                peer=peer,
                app=InputBotAppShortName(bot_id=peer, short_name="join"),
                platform='android',
                write_allowed=True,
                start_param=ref_id
            ))

            auth_url = web_view.url
            tg_web_data = unquote(string=auth_url.split('tgWebAppData=', maxsplit=1)[1].split('&tgWebAppVersion', maxsplit=1)[0])

            me = await self.tg_client.get_me()
            self.tg_client_id = me.id
            
            if self.tg_client.is_connected:
                await self.tg_client.disconnect()

            return ref_id, tg_web_data

        except InvalidSession as error:
            raise error

        except Exception as error:
            logger.error(f"{self.session_name} | Unknown error: {error}")
            await asyncio.sleep(delay=3)

    @error_handler
    async def make_request(self, http_client, method, endpoint=None, url=None, **kwargs):
        full_url = url or f"https://cats-backend-cxblew-prod.up.railway.app{endpoint or ''}"
        response = await http_client.request(method, full_url, **kwargs)
        response.raise_for_status()
        return await response.json()
    
    @error_handler
    async def login(self, http_client, init_data, ref_id):
        http_client.headers['Authorization'] = "tma " + init_data
        user = await self.make_request(http_client, 'GET', endpoint="/user")
        if not user:
            await self.make_request(http_client, 'POST', endpoint=f"/user/create?referral_code={ref_id}")
            await asyncio.sleep(2)
            return await self.login(http_client, init_data, ref_id)
        return user
    
    @error_handler
    async def send_cats(self, http_client):
        avatar_info = await self.make_request(http_client, 'GET', endpoint="/user/avatar")
        if avatar_info:
            attempt_time_str = avatar_info.get('attemptTime', None)
            if attempt_time_str is None:
                time_difference = timedelta(hours=25)
            else:
                attempt_time = datetime.fromisoformat(attempt_time_str.replace('Z', '+00:00'))
                current_time = datetime.now(timezone.utc)
                time_difference = attempt_time - current_time
            if time_difference > timedelta(hours=24):
                img_folder = 'bot/img'
                image_files = [f for f in os.listdir(img_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
                
                if not image_files:
                    logger.info(f"{self.session_name} | No image files found in the 'bot/img' folder")
                    return None
                
                random_image = random.choice(image_files)
                image_path = os.path.join(img_folder, random_image)
                
                mime_type, _ = mimetypes.guess_type(image_path)
                if not mime_type:
                    mime_type = 'application/octet-stream'
                
                
                boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
                form_data = (
                    f'--{boundary}\r\n'
                    f'Content-Disposition: form-data; name="photo"; filename="{random_image}"\r\n'
                    f'Content-Type: {mime_type}\r\n\r\n'
                ).encode('utf-8')
                
                async with aiofiles.open(image_path, 'rb') as file:
                    file_content = await file.read()
                    form_data += file_content
                
                form_data += f'\r\n--{boundary}--\r\n'.encode('utf-8')
                headers = http_client.headers.copy()
                headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
                response = await self.make_request(http_client, 'POST', endpoint="/user/avatar/upgrade", data=form_data, headers=headers)
                avatar_info = await self.make_request(http_client, 'GET', endpoint="/user/avatar")
                return response.get('rewards', 0)
            else:
                time_left = timedelta(hours=24) - time_difference
                hours, remainder = divmod(time_left.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                logger.info(f"{self.session_name} | Time left until next avatar upload: <y>{hours}</y> hours, <y>{minutes}</y> minutes, and <y>{seconds}</y> seconds")
                return None   
    
    async def join_and_mute_tg_channel(self, link: str):
        await asyncio.sleep(delay=15)
        link = link.replace('https://t.me/', "")
        if not self.tg_client.is_connected:
            try:
                await self.tg_client.connect()
            except Exception as error:
                logger.error(f"{self.session_name} | (Task) Connect failed: {error}")
        try:
            chat = await self.tg_client.get_chat(link)
            chat_username = chat.username if chat.username else link
            chat_id = chat.id
            try:
                await self.tg_client.get_chat_member(chat_username, "me")
            except Exception as error:
                if error.ID == 'USER_NOT_PARTICIPANT':
                    await asyncio.sleep(delay=3)
                    response = await self.tg_client.join_chat(link)
                    logger.info(f"{self.session_name} | Joined to channel: <y>{response.username}</y>")
                    
                    try:
                        peer = await self.tg_client.resolve_peer(chat_id)
                        await self.tg_client.invoke(account.UpdateNotifySettings(
                            peer=InputNotifyPeer(peer=peer),
                            settings=InputPeerNotifySettings(mute_until=2147483647)
                        ))
                        logger.info(f"{self.session_name} | Successfully muted chat <y>{chat_username}</y>")
                    except Exception as e:
                        logger.info(f"{self.session_name} | (Task) Failed to mute chat <y>{chat_username}</y>: {str(e)}")
                    
                    
                else:
                    logger.error(f"{self.session_name} | (Task) Error while checking TG group: <y>{chat_username}</y>")

            if self.tg_client.is_connected:
                await self.tg_client.disconnect()
        except Exception as error:
            logger.error(f"{self.session_name} | (Task) Error while join tg channel: {error}")
    
    @error_handler
    async def get_tasks(self, http_client):
        return await self.make_request(http_client, 'GET', endpoint="/tasks/user", data={'group': 'cats'})
    
    @error_handler
    async def done_tasks(self, http_client, task_id, type_):
        return await self.make_request(http_client, 'POST', endpoint=f"/tasks/{task_id}/{type_}", json={})
    
    
    @error_handler
    async def check_proxy(self, http_client: aiohttp.ClientSession) -> None:
        response = await self.make_request(http_client, 'GET', url='https://httpbin.org/ip', timeout=aiohttp.ClientTimeout(5))
        ip = response.get('origin', 'Site is not available')
        logger.info(f"{self.session_name} | Proxy IP: {ip}")
    
    @error_handler
    async def run(self) -> None:
        if settings.USE_RANDOM_DELAY_IN_RUN:
                random_delay = random.randint(settings.RANDOM_DELAY_IN_RUN[0], settings.RANDOM_DELAY_IN_RUN[1])
                logger.info(f"{self.session_name} | Bot will start in <y>{random_delay}s</y>")
                await asyncio.sleep(random_delay)
                
        proxy_conn = ProxyConnector().from_url(self.proxy) if self.proxy else None
        http_client = aiohttp.ClientSession(headers=headers, connector=proxy_conn)
        
        ref_id, init_data = await self.get_tg_web_data()
        if not init_data:
            if not http_client.closed:
                await http_client.close()
            if proxy_conn:
                if not proxy_conn.closed:
                    proxy_conn.close()

        if self.proxy:
            await self.check_proxy(http_client=http_client)
        
        if settings.FAKE_USERAGENT:            
            http_client.headers['User-Agent'] = generate_random_user_agent(device_type='android', browser_type='chrome')

        while True:
            try:
                if http_client.closed:
                    if proxy_conn:
                        if not proxy_conn.closed:
                            proxy_conn.close()

                    proxy_conn = ProxyConnector().from_url(self.proxy) if self.proxy else None
                    http_client = aiohttp.ClientSession(headers=headers, connector=proxy_conn)
                    if settings.FAKE_USERAGENT:            
                        http_client.headers['User-Agent'] = generate_random_user_agent(device_type='android', browser_type='chrome')

                user = await self.login(http_client=http_client, init_data=init_data, ref_id=ref_id)
                if not user:
                    logger.error(f"{self.session_name} | Failed to login")
                    await http_client.close()
                    if proxy_conn:
                        if not proxy_conn.closed:
                            proxy_conn.close()
                    continue
                
                logger.info(f"{self.session_name} | <y>Successfully logged in</y>")
                logger.info(f"{self.session_name} | User ID: <y>{user.get('id')}</y> | Telegram Age: <y>{user.get('telegramAge')}</y> | Points: <y>{user.get('totalRewards')}</y>")
                data_task = await self.get_tasks(http_client=http_client)
                if data_task is not None and data_task.get('tasks', {}):
                    for task in data_task.get('tasks'):
                        if task['completed'] is True:
                            continue
                        id = task.get('id')
                        type = task.get('type')
                        title = task.get('title')
                        reward = task.get('rewardPoints')
                        type_=('check' if type == 'SUBSCRIBE_TO_CHANNEL' else 'complete')
                        if type == 'check':
                            await self.join_and_mute_tg_channel(link=task.get('params').get('channelUrl'))
                            await asyncio.sleep(2)
                        done_task = await self.done_tasks(http_client=http_client, task_id=id, type_=type_)
                        if done_task and (done_task.get('success', False) or done_task.get('completed', False)):
                            logger.info(f"{self.session_name} | Task <y>{title}</y> done! Reward: {reward}")
                                
                else:
                    logger.error(f"{self.session_name} | No tasks")
                
                reward = await self.send_cats(http_client=http_client)
                if reward:
                    logger.info(f"{self.session_name} | Reward from Avatar quest: <y>{reward}</y>")
                
                
                await http_client.close()
                if proxy_conn:
                    if not proxy_conn.closed:
                        proxy_conn.close()
            
            except InvalidSession as error:
                raise error

            except Exception as error:
                logger.error(f"{self.session_name} | Unknown error: {error}")
                await asyncio.sleep(delay=3)
                
            sleep_time = random.randint(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
            logger.info(f"{self.session_name} | Sleep <y>{sleep_time}s</y>")
            await asyncio.sleep(delay=sleep_time)
            
            
            
            

async def run_tapper(tg_client: Client, proxy: str | None):
    try:
        await Tapper(tg_client=tg_client, proxy=proxy).run()
    except InvalidSession:
        logger.error(f"{tg_client.name} | Invalid Session")
