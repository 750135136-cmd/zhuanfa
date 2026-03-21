import os
import re
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, Channel
# ========== 配置项 ==========
api_id = 25559912
api_hash = '22d3bb9665ad7e6a86e89c1445672e07'
session_name = "session"
# 频道配对配置，支持填典藏用户名@xxx、公开用户名、数字频道ID
channels = [
    {
        'source': '@wenan77',
        'target': '@wnffx',
    }
]
max_text_length = 50  # 最大允许的文本长度
forward_interval = 0.8  # 转发间隔（秒），避免风控
media_group_wait_time = 1.5  # 媒体组等待时间
processed_msg_ids = set()  # 已转发消息ID缓存（仅用于去重）
max_cache_size = 1000  # 缓存最大容量，避免内存溢出
media_group_cache = {}  # 媒体组临时缓存
media_group_lock = asyncio.Lock()  # 异步锁
# ========== 全局有效频道列表（已缓存真实ID） ==========
valid_channels = []
# ========== 通用标准化函数 ==========
def standardize_username(username):
    """统一用户名格式：移除@、转小写，兼容大小写/带@不带@"""
    if not username:
        return None
    return username.lstrip('@').lower()
# ========== 文本清洗函数 ==========
def clean_text(text):
    if not text:
        return ""
    # 移除http/https链接、t.me站内链接
    text = re.sub(r'https?://[^\s\u4e00-\u9fa5，。！？；：""\'()（）]+|t\.me/[^\s\u4e00-\u9fa5，。！？；：""\'()（）]+', '', text)
    # 移除Telegram规范的@用户名
    text = re.sub(r'@[a-zA-Z0-9_]{5,32}(?![a-zA-Z0-9_.])', '', text)
    return text.strip()
# ========== 修复后的频道匹配函数（100%兼容典藏频道） ==========
def get_target_channel(source_id):
    """直接用频道真实唯一ID匹配，彻底解决典藏频道匹配问题"""
    for channel in valid_channels:
        if channel['source_id'] == source_id:
            return channel['target']
    return None
# ========== 修复后的频道检查函数（缓存真实ID） ==========
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
            # 仅校验是否为频道类型，不强制要求用户名
            if not isinstance(source_chat, Channel):
                print(f"⚠️  警告：配对{idx+1}的源 {source_config} 不是频道类型，已跳过")
                all_valid = False
                continue
            # 打印频道真实信息，快速定位典藏频道问题
            real_username = source_chat.username if source_chat.username else '无（典藏用户名/无公开用户名）'
            print(f"ℹ️  频道真实信息 | 频道ID：{source_chat.id} | 绑定的公开用户名：@{real_username}")
            print(f"✅ 源频道校验通过，已加入监听列表")
        except Exception as e:
            print(f"❌ 错误：配对{idx+1}的源频道 {source_config} 无法访问，已跳过 | 详情：{e}")
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
            print(f"❌ 错误：配对{idx+1}的目标频道 {target_config} 无法访问，已跳过 | 详情：{e}")
            all_valid = False
            continue
        # 校验通过，存入带真实ID的有效配置（核心修复）
        valid_list.append({
            'source_config': source_config,
            'target': target_config,
            'source_id': source_chat.id  # 缓存源频道真实唯一ID，彻底解决匹配问题
        })
    # 更新全局有效频道列表
    valid_channels = valid_list
    # 结果输出
    if len(valid_channels) > 0:
        print(f"\n✅ 共 {len(valid_channels)} 组频道配置校验通过，将正常启动监听")
    else:
        print("\n❌ 没有可用的频道配置，程序无法启动")
    if all_valid:
        print("✅ 所有频道配置检查通过！")
    else:
        print("⚠️  部分频道配置异常，已自动跳过，有效配对将正常工作")
    return len(valid_channels) > 0
# ========== 核心消息处理逻辑 ==========
async def main():
    async with TelegramClient(session_name, api_id, api_hash) as client:
        # 打印登录状态
        me = await client.get_me()
        print(f"✅ 已成功登录账号：@{me.username} | 用户ID：{me.id}")
        
        # 频道配置检查，无有效频道直接退出
        check_result = await check_channels(client)
        if not check_result:
            return
        
        # 检查重复配置（用真实ID校验，彻底避免重复）
        source_id_list = [c['source_id'] for c in valid_channels]
        if len(source_id_list) != len(set(source_id_list)):
            print("⚠️  警告：检测到重复的源频道配置，重复项仅第一个生效")
        
        # 打印转发规则
        print("\n=== 转发规则已生效 ===")
        print(f"✅ 允许转发：文本≤{max_text_length}字 + 带有图片/视频/媒体的消息（含多图/多视频媒体组）")
        print(f"❌ 禁止转发：纯文字消息、文本超{max_text_length}字的消息（无论是否带媒体）")
        for idx, channel in enumerate(valid_channels):
            print(f"配对{idx+1}：监听 {channel['source_config']} → 转发到 {channel['target']}")
        print("\n机器人已启动，正在监听消息...\n")
        # ========== 媒体组合并转发处理 ==========
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
                # 4. 执行合并转发
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
        # ========== 修复后的消息监听器（用真实ID注册，兼容典藏频道） ==========
        @client.on(events.NewMessage(chats=[c['source_id'] for c in valid_channels]))
        async def handler(event):
            try:
                msg = event.message
                source_chat = event.chat
                source_username = source_chat.username
                source_id = source_chat.id
                # 显示用户名/ID，方便日志查看
                source_name = f"@{source_username}" if source_username else f"频道ID:{source_id}"
                grouped_id = msg.grouped_id
                # 收到消息日志，确认监听器正常工作
                print(f"📥 收到新消息 | 组ID：{grouped_id} | 源：{source_name}")
                # 匹配目标频道（直接用ID匹配，100%命中）
                target_channel = get_target_channel(source_id)
                if not target_channel:
                    print(f"⏭️  已拦截 | 源：{source_name} | 原因：未匹配到对应的目标频道")
                    return
                # ========== 处理媒体组（多图/多视频消息） ==========
                if grouped_id:
                    async with media_group_lock:
                        if grouped_id not in media_group_cache:
                            media_group_cache[grouped_id] = {
                                'msg_list': [],
                                'source_chat': source_chat,
                                'target_channel': target_channel,
                                'source_name': source_name
                            }
                            # 启动等待任务，合并媒体组
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
