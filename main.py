import os
from telethon import TelegramClient, events
import re

# 直接在代码中填写 API_ID 和 API_HASH
api_id = 25559912  # 替换成你的 API_ID
api_hash = '22d3bb9665ad7e6a86e89c1445672e07'  # 替换成你的 API_HASH

# 设置监听和目标频道
channels = [
    {
        'source': '@wenan77',  # 第一个监听频道
        'target': '@wnffx',  # 第一个目标频道
    },
    {
        'source': '@sucai75',  # 第二个监听频道
        'target': '@hwsuc',  # 第二个目标频道
    }
]

# 设置最大消息字符数，超过则不转发
max_length = 50

# 清理掉链接或用户提及等不需要的内容
def clean_text(text):
    # 去除 URL 和 t.me 链接
    text = re.sub(r'https?://\S+|t\.me/\S+', '', text)
    # 去除 @用户名
    text = re.sub(r'@\S+', '', text)
    return text.strip()

# 创建客户端
client = TelegramClient("session", api_id, api_hash)

# 监听并转发消息
@client.on(events.NewMessage(chats=[channel['source'] for channel in channels]))
async def handler(event):
    msg = event.message
    source_channel = event.chat.username
    target_channel = None

    # 找到当前消息对应的目标频道
    for channel in channels:
        if source_channel == channel['source'][1:]:  # 去掉 @ 符号
            target_channel = channel['target']
            break

    if not target_channel:
        return

    text = msg.text or ""

    # 清理消息文本（去掉链接和@提及）
    text = clean_text(text)

    # 如果文本超过最大字符数，跳过
    if len(text) > max_length:
        return

    try:
        # 如果是文本消息
        if msg.text:
            await client.send_message(target_channel, text)
            print(f"转发文本: {text[:30]} 到 {target_channel}")  # 输出前30个字符
        # 如果是媒体消息（图片/视频）
        elif msg.media:
            await client.send_file(target_channel, msg.media, caption=text)
            print(f"转发媒体消息到 {target_channel}")  # 输出转发的媒体消息
    except Exception as e:
        print("错误:", e)

# 启动客户端
async def main():
    await client.start()
    print("机器人已启动")
    await client.run_until_disconnected()

# 运行
import asyncio
asyncio.run(main())