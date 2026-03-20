import os
import re
import asyncio
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, Channel

# ========== 核心配置项（请务必先核对这里） ==========
api_id = 25559912
api_hash = '22d3bb9665ad7e6a86e89c1445672e07'
session_name = "session"
# 监听-目标频道配对
# 【强烈推荐】source优先用带-100前缀的完整频道ID，比如 '-1001697824910'
# 【格式要求】ID必须用字符串格式（带引号），用户名可以直接写 '@xxx'
channels = [
    {
        'source': '@wenan77',  # 替换为你核对好的源频道ID/用户名
        'target': '@wnffx',    # 替换为你核对好的目标频道ID/用户名
    },
    {
        'source': '@sucai75',
        'target': '@hwsuc',
    }
]
max_text_length = 150  # 最大允许的文本长度
forward_interval = 0.8  # 转发间隔（秒），避免风控
media_group_wait_time = 1.5  # 媒体组等待时间
# ========== 每日转发限额配置 ==========
DAILY_MAX_FORWARD = 10  # 每个源频道每日最多转发条数
# ==========================================
processed_msg_ids = set()
max_cache_size = 1000
media_group_cache = {}
media_group_lock = asyncio.Lock()
count_lock = asyncio.Lock()

# ========== 内存计数管理 ==========
current_count_date = datetime.now().strftime("%Y-%m-%d")
daily_forward_count = {}

async def get_today_count(source_id):
    global current_count_date, daily_forward_count
    async with count_lock:
        today = datetime.now().strftime("%Y-%m-%d")
        if current_count_date != today:
            current_count_date = today
            daily_forward_count = {}
        return daily_forward_count.get(str(source_id), 0)

async def add_forward_count(source_id):
    global daily_forward_count
    async with count_lock:
        source_id_str = str(source_id)
        daily_forward_count[source_id_str] = daily_forward_count.get(source_id_str, 0) + 1
        return daily_forward_count[source_id_str]

# ========== 通用工具函数 ==========
def standardize_username(username):
    if not username:
        return None
    return username.lstrip('@').lower()

# ========== 文本清洗函数 ==========
def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'https?://[^\s\u4e00-\u9fa5，。！？；：""\'()（）]+|t\.me/[^\s\u4e00-\u9fa5，。！？；：""\'()（）]+', '', text)
    text = re.sub(r'@[a-zA-Z0-9_]{5,32}(?![a-zA-Z0-9_.])', '', text)
    return text.strip()

# ========== 频道匹配工具函数 ==========
def get_target_channel(source_id, source_username):
    std_source_username = standardize_username(source_username)
    for channel in valid_channels:
        config_source = channel['source'].strip()
        if config_source.lstrip('-').isdigit():
            if int(config_source) == source_id:
                return channel['target']
        else:
            std_config_name = standardize_username(config_source)
            if std_source_username and std_config_name == std_source_username:
                return channel['target']
    return None

# ========== 频道检查函数（优化版，错误频道直接跳过） ==========
async def check_channels(client):
    print("=== 正在检查频道配置 ===")
    valid_list = []
    for idx, channel in enumerate(channels):
        source = channel['source']
        target = channel['target']
        print(f"\n--- 检查配对{idx+1}：监听 {source} → 转发到 {target} ---")
        # 检查源频道
        try:
            source_chat = await client.get_entity(source)
            if not isinstance(source_chat, Channel):
                print(f"⚠️  跳过：源 {source} 不是频道类型")
                continue
            print(f"✅ 源频道校验通过：{source} | 频道ID：{source_chat.id}")
        except Exception as e:
            print(f"❌ 跳过：源频道 {source} 无法访问 | 详情：{e}")
            continue
        # 检查目标频道
        try:
            target_chat = await client.get_entity(target)
            if not isinstance(target_chat, Channel):
                print(f"⚠️  跳过：目标 {target} 不是频道类型")
                continue
            print(f"✅ 目标频道校验通过：{target}")
        except Exception as e:
            print(f"❌ 跳过：目标频道 {target} 无法访问 | 详情：{e}")
            continue
        # 校验通过，加入有效列表
        valid_list.append(channel)
    # 全局有效频道列表
    global valid_channels
    valid_channels = valid_list
    if len(valid_channels) > 0:
        print(f"\n✅ 共 {len(valid_channels)} 组频道配置校验通过，将正常启动监听")
        return True
    else:
        print("\n❌ 没有可用的频道配置，程序无法启动")
        return False

# ========== 核心消息处理逻辑 ==========
async def main():
    async with TelegramClient(session_name, api_id, api_hash) as client:
        # 登录信息
        me = await client.get_me()
        print(f"✅ 已成功登录账号：@{me.username} | 用户ID：{me.id}")
        
        print(f"✅ 每日转发限额功能已启动，每个源频道每日最多转发 {DAILY_MAX_FORWARD} 条")
        
        # 频道配置检查，失败直接退出
        check_result = await check_channels(client)
        if not check_result:
            return
        
        # 打印转发规则
        print("\n=== 最终生效的转发规则 ===")
        print(f"✅ 允许转发：文本≤{max_text_length}字 + 带有图片/视频/媒体的消息（含多图/多视频媒体组）")
        print(f"❌ 禁止转发：纯文字消息、文本超{max_text_length}字的消息（无论是否带媒体）")
        print(f"📊 每日限额：每个监听频道当日最多转发 {DAILY_MAX_FORWARD} 条，满额后自动拦截，次日重置")
        for idx, channel in enumerate(valid_channels):
            print(f"配对{idx+1}：监听 {channel['source']} → 转发到 {channel['target']}")
        print("\n机器人已启动，正在监听消息...\n")

        # ========== 媒体组处理 ==========
        async def process_media_group(grouped_id):
            try:
                await asyncio.sleep(media_group_wait_time)
                
                async with media_group_lock:
                    if grouped_id not in media_group_cache:
                        return
                    group_data = media_group_cache.pop(grouped_id)
                
                msg_list = group_data['msg_list']
                source_chat = group_data['source_chat']
                target_channel = group_data['target_channel']
                source_name = group_data['source_name']
                source_id = source_chat.id

                # 去重校验
                first_msg = msg_list[0]
                if first_msg.id in processed_msg_ids:
                    print(f"⏭️  已跳过 | 源：{source_name} | 原因：重复媒体组消息")
                    return
                processed_msg_ids.add(first_msg.id)
                if len(processed_msg_ids) > max_cache_size:
                    processed_msg_ids.pop()

                # 每日限额校验
                today_count = await get_today_count(source_id)
                if today_count >= DAILY_MAX_FORWARD:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：今日转发已达上限 {today_count}/{DAILY_MAX_FORWARD} 条")
                    return

                # 媒体有效性校验
                valid_media = []
                for msg in msg_list:
                    if isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                        valid_media.append(msg.media)
                if not valid_media:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：媒体组内无有效图片/视频媒体")
                    return

                # 文本校验
                raw_text = first_msg.text or ""
                cleaned_text = clean_text(raw_text)
                if len(cleaned_text) > max_text_length:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：文本长度{len(cleaned_text)}，超过{max_text_length}字限制")
                    return

                # 执行转发
                await asyncio.sleep(forward_interval)
                await client.send_message(
                    target_channel,
                    message=cleaned_text,
                    file=valid_media,
                    silent=True
                )
                # 转发成功，计数+1
                new_count = await add_forward_count(source_id)
                print(f"✅ 媒体组转发成功 | 源：{source_name} → 目标：{target_channel} | 今日进度：{new_count}/{DAILY_MAX_FORWARD} | 文案预览：{cleaned_text[:30]}")
            except Exception as e:
                print(f"❌ 媒体组处理失败 | 错误详情：{e}")

        # ========== 消息监听器（仅监听有效频道，彻底避免报错） ==========
        @client.on(events.NewMessage(chats=[c['source'] for c in valid_channels]))
        async def handler(event):
            try:
                msg = event.message
                source_chat = event.chat
                source_name = f"@{source_chat.username}" if source_chat.username else f"频道ID:{source_chat.id}"
                source_id = source_chat.id
                grouped_id = msg.grouped_id

                print(f"📥 收到新消息组ID：{grouped_id}")

                # 匹配目标频道
                target_channel = get_target_channel(source_chat.id, source_chat.username)
                if not target_channel:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：未匹配到对应的目标频道")
                    return

                # 每日限额前置校验
                today_count = await get_today_count(source_id)
                if today_count >= DAILY_MAX_FORWARD:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：今日转发已达上限 {today_count}/{DAILY_MAX_FORWARD} 条")
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
                            asyncio.create_task(process_media_group(grouped_id))
                        media_group_cache[grouped_id]['msg_list'].append(msg)
                    print(f"📦 已加入媒体组缓存 | 组ID：{grouped_id} | 当前组内媒体数：{len(media_group_cache[grouped_id]['msg_list'])}")
                    return

                # 单消息去重
                if msg.id in processed_msg_ids:
                    print(f"⏭️  已跳过 | 源：{source_name} | 原因：重复消息")
                    return
                processed_msg_ids.add(msg.id)
                if len(processed_msg_ids) > max_cache_size:
                    processed_msg_ids.pop()

                # 纯文字拦截
                if not msg.media:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：纯文字消息，无图片/视频媒体")
                    return

                # 媒体类型校验
                if not isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：非图片/视频类媒体")
                    return

                # 文本校验
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
                # 转发成功，计数+1
                new_count = await add_forward_count(source_id)
                print(f"✅ 单媒体转发成功 | 源：{source_name} → 目标：{target_channel} | 今日进度：{new_count}/{DAILY_MAX_FORWARD} | 文案预览：{cleaned_text[:30]}")
            except Exception as e:
                print(f"❌ 消息处理失败 | 错误详情：{e}")

        await client.run_until_disconnected()

# 程序入口
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ 程序已手动停止，会话已安全关闭")
    except Exception as e:
        print(f"❌ 程序异常退出：{e}")
