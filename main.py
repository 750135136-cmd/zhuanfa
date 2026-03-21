import os
import re
import asyncio
from collections import deque
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, Channel
# ========== 配置项 ==========
api_id = 25559912
api_hash = '22d3bb9665ad7e6a86e89c1445672e07'
session_name = "session"
# 频道配对配置
channels = [
    {
        'source': '@wenan77',
        'target': '@wnffx',
    },
    {
        'source': '@xdgd18',
        'target': '@hrgxx',
    }
]
max_text_length = 100  # 放宽到100字，避免正常文案被拦截
forward_interval = 3  # 转发间隔≥3秒，避免风控和API限流
media_group_wait_time = 4  # 媒体组等待时间延长到4秒，确保收全所有媒体
max_cache_size = 1000
# ========== 全局缓存 ==========
processed_msg_ids = deque(maxlen=max_cache_size)
media_group_cache = {}
media_group_lock = asyncio.Lock()
valid_channels = []
# ========== 通用函数 ==========
def standardize_username(username):
    if not username:
        return None
    return username.lstrip('@').lower()

def clean_text(text):
    if not text:
        return ""
    # 移除链接和@用户名，保留正常文案
    text = re.sub(r'https?://\S+|t\.me/\S+', '', text)
    text = re.sub(r'@[a-zA-Z0-9_]{5,32}', '', text)
    text = re.sub(r'\n+', '\n', text).strip()
    return text

def get_target_channel(source_id):
    for channel in valid_channels:
        if channel['source_id'] == source_id:
            return channel['target']
    return None
# ========== 频道检查 ==========
async def check_channels(client):
    print("=== 正在检查频道配置 ===")
    global valid_channels
    valid_list = []
    all_valid = True
    for idx, channel in enumerate(channels):
        source_config = channel['source']
        target_config = channel['target']
        print(f"\n--- 检查配对{idx+1}：监听 {source_config} → 转发到 {target_config} ---")
        # 检查源频道
        try:
            source_chat = await client.get_entity(source_config)
            if not isinstance(source_chat, Channel):
                print(f"⚠️  警告：配对{idx+1}的源 {source_config} 不是频道类型，已跳过")
                all_valid = False
                continue
            real_username = source_chat.username if source_chat.username else '无（典藏频道）'
            print(f"ℹ️  频道真实ID：{source_chat.id} | 公开用户名：@{real_username}")
            print(f"✅ 源频道校验通过")
        except Exception as e:
            print(f"❌ 源频道 {source_config} 访问失败 | 详情：{e}")
            all_valid = False
            continue
        # 检查目标频道
        try:
            target_chat = await client.get_entity(target_config)
            if not isinstance(target_chat, Channel):
                print(f"⚠️  警告：配对{idx+1}的目标 {target_config} 不是频道类型，已跳过")
                all_valid = False
                continue
            print(f"✅ 目标频道校验通过")
        except Exception as e:
            print(f"❌ 目标频道 {target_config} 访问失败 | 详情：{e}")
            all_valid = False
            continue
        # 存入有效配置
        valid_list.append({
            'source_config': source_config,
            'target': target_config,
            'source_id': source_chat.id
        })
    valid_channels = valid_list
    if len(valid_channels) > 0:
        print(f"\n✅ 共 {len(valid_channels)} 组频道配置生效")
    else:
        print("\n❌ 无可用频道配置，程序无法启动")
    return len(valid_channels) > 0
# ========== 核心逻辑 ==========
async def main():
    client = TelegramClient(
        session_name, 
        api_id, 
        api_hash,
        auto_reconnect=True,
        connection_retries=10,
        retry_delay=5,
        timeout=30
    )
    async with client:
        # 登录信息
        me = await client.get_me()
        print(f"✅ 已登录账号：@{me.username} | 用户ID：{me.id}")
        
        # 频道检查
        check_result = await check_channels(client)
        if not check_result:
            return
        
        # 重复配置提醒
        source_id_list = [c['source_id'] for c in valid_channels]
        if len(source_id_list) != len(set(source_id_list)):
            print("⚠️  检测到重复的源频道，重复项仅第一个生效")
        
        # 规则打印
        print("\n=== 转发规则已生效 ===")
        print(f"✅ 允许转发：带图片/视频的消息（含多图媒体组），清洗后文本≤{max_text_length}字")
        print(f"❌ 禁止转发：纯文字消息、文本超{max_text_length}字的消息")
        for idx, channel in enumerate(valid_channels):
            print(f"配对{idx+1}：监听 {channel['source_config']} → 转发到 {channel['target']}")
        print("\n机器人已启动，正在监听消息...\n")

        # 媒体组处理
        async def process_media_group(grouped_id):
            try:
                # 等待足够时间，确保同组所有媒体全部到达
                await asyncio.sleep(media_group_wait_time)
                
                async with media_group_lock:
                    if grouped_id not in media_group_cache:
                        return
                    group_data = media_group_cache.pop(grouped_id)
                
                msg_list = group_data['msg_list']
                source_chat = group_data['source_chat']
                target_channel = group_data['target_channel']
                source_name = group_data['source_name']

                # 去重校验
                first_msg = msg_list[0]
                if first_msg.id in processed_msg_ids:
                    print(f"⏭️  已跳过 | 源：{source_name} | 重复媒体组")
                    return
                processed_msg_ids.append(first_msg.id)

                # 提取有效媒体
                valid_media = []
                for msg in msg_list:
                    if isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                        valid_media.append(msg.media)
                if not valid_media:
                    print(f"⏭️  已拦截 | 源：{source_name} | 无有效媒体")
                    return

                # 文本处理（取第一条消息的文案，确保不丢失）
                raw_text = first_msg.text or ""
                cleaned_text = clean_text(raw_text)
                if len(cleaned_text) > max_text_length:
                    print(f"⏭️  已拦截 | 源：{source_name} | 文本长度{len(cleaned_text)}，超过限制")
                    return

                # 执行合并转发
                await asyncio.sleep(forward_interval)
                await client.send_message(
                    target_channel,
                    message=cleaned_text,
                    file=valid_media,
                    silent=True
                )
                print(f"✅ 媒体组转发成功 | 源：{source_name} → 目标：{target_channel} | 媒体数：{len(valid_media)} | 文案：{cleaned_text[:30]}")
            except Exception as e:
                print(f"❌ 媒体组处理失败 | 详情：{e}")
                # 异常时清理缓存，避免残留
                async with media_group_lock:
                    if grouped_id in media_group_cache:
                        del media_group_cache[grouped_id]

        # 消息监听器
        @client.on(events.NewMessage(chats=[c['source_id'] for c in valid_channels]))
        async def handler(event):
            try:
                msg = event.message
                source_chat = event.chat
                source_id = source_chat.id
                source_name = f"@{source_chat.username}" if source_chat.username else f"频道ID:{source_id}"
                grouped_id = msg.grouped_id

                print(f"📥 收到新消息 | 组ID：{grouped_id} | 源：{source_name}")

                # 匹配目标频道
                target_channel = get_target_channel(source_id)
                if not target_channel:
                    print(f"⏭️  已拦截 | 源：{source_name} | 无匹配目标频道")
                    return

                # 处理媒体组
                if grouped_id:
                    async with media_group_lock:
                        if grouped_id not in media_group_cache:
                            media_group_cache[grouped_id] = {
                                'msg_list': [],
                                'source_chat': source_chat,
                                'target_channel': target_channel,
                                'source_name': source_name
                            }
                            # 仅第一次收到该组消息时，启动处理任务
                            asyncio.create_task(process_media_group(grouped_id))
                        # 把当前媒体加入缓存
                        media_group_cache[grouped_id]['msg_list'].append(msg)
                    print(f"📦 已加入媒体组 | 组ID：{grouped_id} | 当前组内媒体数：{len(media_group_cache[grouped_id]['msg_list'])}")
                    return

                # 处理单媒体消息
                if msg.id in processed_msg_ids:
                    print(f"⏭️  已跳过 | 源：{source_name} | 重复消息")
                    return
                processed_msg_ids.append(msg.id)

                if not msg.media:
                    print(f"⏭️  已拦截 | 源：{source_name} | 纯文字消息")
                    return

                if not isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                    print(f"⏭️  已拦截 | 源：{source_name} | 非图片/视频媒体")
                    return

                # 文本处理
                raw_text = msg.text or ""
                cleaned_text = clean_text(raw_text)
                if len(cleaned_text) > max_text_length:
                    print(f"⏭️  已拦截 | 源：{source_name} | 文本长度{len(cleaned_text)}，超过限制")
                    return

                # 转发
                await asyncio.sleep(forward_interval)
                await client.send_message(
                    target_channel,
                    message=cleaned_text,
                    file=msg.media,
                    silent=True
                )
                print(f"✅ 单媒体转发成功 | 源：{source_name} → 目标：{target_channel} | 文案：{cleaned_text[:30]}")
            except Exception as e:
                print(f"❌ 消息处理失败 | 详情：{e}")

        await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ 程序已手动停止")
    except Exception as e:
        print(f"❌ 程序异常退出：{e}")
