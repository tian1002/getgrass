import asyncio
import json
import os
import random
import ssl
import time
import uuid

from faker import Faker
from loguru import logger
from websockets_proxy import Proxy, proxy_connect



from config.redis import redis_device_id, redis_prefix_agent
from util.common_utils import read_users_from_files, to_md5
from util.redis_util import RedisUtils

# 存储已连接的 WebSocket 对象的列表
connected_websockets = []

redis_util = RedisUtils().init_redis()


async def send_message(websocket, message):
    """
    发送消息到 WebSocket 服务器
    """
    message_str = json.dumps(message)
    await websocket.send(message_str)


async def receive_message(websocket):
    """
    接收 WebSocket 服务器的消息
    """
    response = await websocket.recv()
    return json.loads(response)


async def authenticate(websocket, auth_id, device_id, user_id, agent):
    """
    发送认证消息到 WebSocket 服务器
    """
    auth_message = {
        "id": auth_id,
        "origin_action": "AUTH",
        "result": {
            "browser_id": device_id,
            "user_id": user_id,
            "user_agent": agent,
            "timestamp": int(time.time()),
            "device_type": "desktop",
            "version": "4.28.2"
        }
    }
    await send_message(websocket, auth_message)


async def task_single(user):
    proxy_ip = user['proxy_ip']
    user_id = user['user']
    device_id = redis_util.get(redis_device_id + to_md5(proxy_ip))
    if device_id is None:
        device_id = str(uuid.uuid4())
        redis_util.set(redis_device_id + to_md5(proxy_ip), device_id)
    device_id = redis_util.get(redis_device_id + to_md5(proxy_ip))

    agent = redis_util.get(redis_prefix_agent + to_md5(proxy_ip))
    if agent is None:
        agent = Faker().chrome()
        redis_util.set(redis_prefix_agent + to_md5(proxy_ip), agent)
    agent = redis_util.get(redis_prefix_agent + to_md5(proxy_ip))
    logger.info(f"当年固定设备id:{device_id}, 固定浏览器agent:{agent}")
    uri_options = ["wss://proxy.wynd.network:4650/"]
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    # 第一步发起sock链接
    uri = random.choice(uri_options)
    myproxy = Proxy.from_url(proxy_ip)
    while True:
        try:
            async with proxy_connect(uri, ssl=ssl_context, extra_headers={"User-Agent": agent}, proxy=myproxy,
                                     proxy_conn_timeout=30) as websocket:
                # 将连接加入到已连接的 WebSocket 列表中
                connected_websockets.append(websocket)

                # 第1步：接收平台auth请求响应
                auth_response = await receive_message(websocket)
                logger.info(f"{myproxy.proxy_host} 平台auth认证响应成功：{auth_response}")
                await asyncio.sleep(random.randint(10, 20) / 10)
                # 第3步：进行auth请求
                await authenticate(websocket, auth_response["id"], device_id, user_id, agent)
                await asyncio.sleep(20)

                """
                业务逻辑处理
                """
                # 第2步：发送ping请求
                message = {
                    "id": str(uuid.uuid4()),
                    "version": "1.0.0",
                    "action": "PING",
                    "data": {}
                }
                await send_message(websocket, message)

                while True:
                    # 第4步：得到认证成功请求响应
                    pong_response = await receive_message(websocket)
                    logger.info(f"{myproxy.proxy_host} 报文响应成功：{pong_response}")
                    await asyncio.sleep(random.randint(1, 9) / 10)
                    pong_message = {
                        "id": pong_response["id"],
                        "origin_action": "PONG"
                    }
                    # 第5步：回复平台已得到认证成功请求响应
                    await send_message(websocket, pong_message)

                    await asyncio.sleep(random.randint(180, 250) / 10)

                    ping_message = {
                        "id": str(uuid.uuid4()),
                        "version": "1.0.0",
                        "action": "PING",
                        "data": {}
                    }
                    # 第6步：发送心跳包
                    await send_message(websocket, ping_message)
                    await asyncio.sleep(random.randint(1, 9) / 10)
        except Exception as e:
            sleep_time = random.randint(5, 15)
            logger.error(f"代理连接失败，准备重连 {myproxy.proxy_host}: {myproxy.proxy_port} ，异常信息：{e}")
            await asyncio.sleep(sleep_time)


async def task_multi(file_names):
    current_directory = os.path.dirname(os.path.abspath(__file__))
    # 从多个文件中读取钱包数据
    users = read_users_from_files(file_names, current_directory, "wallet")
    if users is None or len(users) <= 0:
        raise ValueError("请导入用户和代理！！！！")
    # 打乱数据顺序
    random.shuffle(users)
    semaphore = asyncio.Semaphore(len(users))
    async def limited_task(wallet):
        async with semaphore:
            try:
                await task_single(wallet)
            except Exception as e:
                logger.error(e)

    # 并发处理
    tasks = [limited_task(user) for user in users]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    file_names = ['users.txt']
    loop = asyncio.get_event_loop()
    loop.run_until_complete(task_multi(file_names))
