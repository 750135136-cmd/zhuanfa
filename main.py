import os
import re
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, Channel

# ========== 配置项 ==========
api_id = 25559912
api_hash = '22d3bb9665ad7e6a86e89c1445672e07'
session_name = "session"  # 复用你的session.session文件
# 监听-目标频道配对，严格核对用户名
channels = [
    {
        'source': '@wenan77',
        'target': '@wnffx',
    },
    {
        'source': '@sucai75',
        'target': '@hwsuc',
    }
]
max_text_length = 150  # 最大允许的文本长度
forward_interval = 0.8  # 转发间隔（秒），避免风控
media_group_wait_time = 1.5  # 媒体组等待时间（网络波动可改2秒），确保所有媒体接收完成
processed_msg_ids = set()  # 已转发消息ID缓存
max_cache_size = 1000  # 缓存最大容量，避免内存溢出
media_group_cache = {}  # 媒体组临时缓存，合并同组多图/多视频
media_group_lock = asyncio.Lock()  # 异步锁，避免缓存并发冲突

# ========== 文本清洗函数 ==========
def clean_text(text):
    if not text:
        return ""
    # 移除http/https链接、t.me站内链接
    text = re.sub(r'https?://[^\s\u4e00-\u9fa5，。！？；：""\'()（）]+|t\.me/[^\s\u4e00-\u9fa5，。！？；：""\'()（）]+', '', text)
    # 移除Telegram规范的@用户名
    text = re.sub(r'@[a-zA-Z0-9_]{5,32}(?![a-zA-Z0-9_.])', '', text)
    return text.strip()

# ========== 频道匹配工具函数 ==========
def get_target_channel(source_id, source_username):
    source_username = source_username.lower() if source_username else None
    for channel in channels:
        config_source = channel['source'].strip()
        # 匹配数字ID的私有频道
        if config_source.isdigit():
            if int(config_source) == source_id:
                return channel['target']
        # 匹配用户名（兼容带@/不带@、大小写不敏感）
        else:
            config_name = config_source.lstrip('@').lower()
            if source_username and config_name == source_username:
                return channel['target']
    return None

# ========== 简化版频道检查 ==========
async def check_channels(client):
    print("=== 正在检查频道配置 ===")
    all_valid = True
    for idx, channel in enumerate(channels):
        source = channel['source']
        target = channel['target']
        # 检查源频道是否可访问
        try:
            source_chat = await client.get_entity(source)
            if not isinstance(source_chat, Channel):
                print(f"⚠️  警告：配对{idx+1}的源 {source} 不是频道类型，请检查配置")
        except Exception as e:
            print(f"❌ 错误：配对{idx+1}的源频道 {source} 无法访问，请确认账号已加入该频道 | 详情：{e}")
            all_valid = False
        # 检查目标频道是否可访问
        try:
            await client.get_entity(target)
        except Exception as e:
            print(f"❌ 错误：配对{idx+1}的目标频道 {target} 无法访问，请确认账号已加入该频道 | 详情：{e}")
            all_valid = False
    if all_valid:
        print("✅ 所有频道配置检查通过！")
    else:
        print("⚠️  部分频道配置异常，程序仍会启动，异常配对将无法正常转发")
    return all_valid

# ========== 核心消息处理逻辑 ==========
async def main():
    async with TelegramClient(session_name, api_id, api_hash) as client:
        # 打印登录状态
        me = await client.get_me()
        print(f"✅ 已成功登录账号：@{me.username} | 用户ID：{me.id}")
        
        # 频道配置检查
        await check_channels(client)
        
        # 检查重复配置
        source_list = [c['source'].lstrip('@').lower() for c in channels]
        if len(source_list) != len(set(source_list)):
            print("⚠️  警告：检测到重复的源频道配置，重复项仅第一个生效")
        
        # 打印转发规则
        print("\n=== 转发规则已生效 ===")
        print(f"✅ 允许转发：文本≤{max_text_length}字 + 带有图片/视频/媒体的消息（含多图/多视频媒体组）")
        print(f"❌ 禁止转发：纯文字消息、文本超{max_text_length}字的消息（无论是否带媒体）")
        for idx, channel in enumerate(channels):
            print(f"配对{idx+1}：监听 {channel['source']} → 转发到 {channel['target']}")
        print("\n机器人已启动，正在监听消息...\n")

        # ========== 媒体组合并转发处理（修复作用域问题，放在内部可访问client） ==========
        async def process_media_group(grouped_id):
            """处理完整的媒体组，合并转发为同一条消息"""
            try:
                # 等待所有媒体接收完成
                await asyncio.sleep(media_group_wait_time)
                
                async with media_group_lock:
                    if grouped_id not in media_group_cache:
                        return
                    group_data = media_group_cache.pop(grouped_id)
                
                msg_list = group_data['msg_list']
                source_chat = group_data['source_chat']
                target_channel = group_data['target_channel']
                source_name = group_data['source_name']

                # 1. 去重校验
                first_msg = msg_list[0]
                if first_msg.id in processed_msg_ids:
                    print(f"⏭️  已跳过 | 源：{source_name} | 原因：重复媒体组消息")
                    return
                processed_msg_ids.add(first_msg.id)
                if len(processed_msg_ids) > max_cache_size:
                    processed_msg_ids.pop()

                # 2. 校验媒体有效性
                valid_media = []
                for msg in msg_list:
                    if isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                        valid_media.append(msg.media)
                if not valid_media:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：媒体组内无有效图片/视频媒体")
                    return

                # 3. 文本清洗与长度校验
                raw_text = first_msg.text or ""
                cleaned_text = clean_text(raw_text)
                if len(cleaned_text) > max_text_length:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：文本长度{len(cleaned_text)}，超过{max_text_length}字限制")
                    return

                # 4. 执行合并转发（和原消息结构完全一致）
                await asyncio.sleep(forward_interval)
                await client.send_message(
                    target_channel,
                    message=cleaned_text,
                    file=valid_media,
                    silent=True
                )
                print(f"✅ 媒体组转发成功 | 源：{source_name} → 目标：{target_channel} | 媒体数量：{len(valid_media)} | 文案预览：{cleaned_text[:30]}")
            except Exception as e:
                print(f"❌ 媒体组处理失败 | 错误详情：{e}")

        # ========== 注册消息监听器 ==========
        @client.on(events.NewMessage(chats=[c['source'] for c in channels]))
        async def handler(event):
            try:
                msg = event.message
                source_chat = event.chat
                source_name = f"@{source_chat.username}" if source_chat.username else f"频道ID:{source_chat.id}"
                grouped_id = msg.grouped_id

                # 【必看日志】收到消息就打印，确认监听器正常工作
                print(f"📥 收到新消息组ID：{grouped_id}")

                # 匹配目标频道
                target_channel = get_target_channel(source_chat.id, source_chat.username)
                if not target_channel:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：未匹配到对应的目标频道")
                    return

                # ========== 处理媒体组（多图/多视频消息） ==========
                if grouped_id:
                    async with media_group_lock:
                        # 该媒体组首次收到，初始化缓存
                        if grouped_id not in media_group_cache:
                            media_group_cache[grouped_id] = {
                                'msg_list': [],
                                'source_chat': source_chat,
                                'target_channel': target_channel,
                                'source_name': source_name
                            }
                            # 启动等待任务，正确的Python async写法
                            asyncio.create_task(process_media_group(grouped_id))
                        # 将当前媒体加入对应媒体组缓存
                        media_group_cache[grouped_id]['msg_list'].append(msg)
                    print(f"📦 已加入媒体组缓存 | 组ID：{grouped_id} | 当前组内媒体数：{len(media_group_cache[grouped_id]['msg_list'])}")
                    return

                # ========== 处理单媒体消息（单个图片/视频） ==========
                # 消息去重
                if msg.id in processed_msg_ids:
                    print(f"⏭️  已跳过 | 源：{source_name} | 原因：重复消息")
                    return
                processed_msg_ids.add(msg.id)
                if len(processed_msg_ids) > max_cache_size:
                    processed_msg_ids.pop()
                
                # 核心规则1：纯文字消息直接拦截
                if not msg.media:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：纯文字消息，无图片/视频媒体")
                    return
                
                # 核心规则2：仅允许图片、视频类媒体
                if not isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：非图片/视频类媒体")
                    return
                
                # 文本清洗与长度校验
                raw_text = msg.text or ""
                cleaned_text = clean_text(raw_text)
                if len(cleaned_text) > max_text_length:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：文本长度{len(cleaned_text)}，超过{max_text_length}字限制")
                    return
                
                # 执行单媒体转发
                await asyncio.sleep(forward_interval)
                await client.send_message(
                    target_channel,
                    message=cleaned_text,
                    file=msg.media,
                    silent=True
                )
                print(f"✅ 单媒体转发成功 | 源：{source_name} → 目标：{target_channel} | 文案预览：{cleaned_text[:30]}")
            except Exception as e:
                print(f"❌ 消息处理失败 | 错误详情：{e}")
        
        # 保持客户端运行
        await client.run_until_disconnected()

# 程序入口
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ 程序已手动停止，会话已安全关闭")
    except Exception as e:
        print(f"❌ 程序异常退出：{e}")
