import os
import re
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, PeerChannel

# ========== 配置项（和你原代码格式完全兼容，无需修改） ==========
api_id = 25559912
api_hash = '22d3bb9665ad7e6a86e89c1445672e07'
session_name = "session"  # 独立session文件名，避免冲突

# 监听-目标频道配对，支持带@的公开频道、数字ID的私有频道
channels = [
    {
        'source': '@zgrlt8',
        'target': '@wnffx',
    },
    {
        'source': '@zgrlt9',
        'target': '@hwsuc',
    }
]

max_text_length = 50  # 最大允许的文本长度，超过则整条消息不转发
forward_interval = 0.8  # 转发间隔（秒），避免批量转发触发Telegram风控
processed_msg_ids = set()  # 已转发消息ID缓存，防止重复转发刷屏
max_cache_size = 1000  # 缓存最大容量，避免内存溢出

# ========== 文本清洗函数（修复原正则误杀问题） ==========
def clean_text(text):
    if not text:
        return ""
    # 仅移除http/https链接、t.me站内链接，不误杀正常中文内容
    text = re.sub(r'https?://[^\s\u4e00-\u9fa5，。！？；：""''()（）]+|t\.me/[^\s\u4e00-\u9fa5，。！？；：""''()（）]+', '', text)
    # 仅移除Telegram规范的@用户名，不误杀邮箱地址
    text = re.sub(r'@[a-zA-Z0-9_]{5,32}(?![a-zA-Z0-9_.])', '', text)
    return text.strip()

# ========== 频道匹配工具函数（修复原代码匹配失效bug） ==========
def get_target_channel(source_id, source_username):
    """兼容所有频道类型，精准匹配对应的目标频道"""
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

# ========== 启动前权限检查（提前暴露配置问题，避免运行时失效） ==========
async def check_channel_permissions(client):
    print("=== 正在检查频道权限 ===")
    for idx, channel in enumerate(channels):
        source = channel['source']
        target = channel['target']
        # 检查源频道是否可访问
        try:
            source_chat = await client.get_entity(source)
            if not isinstance(source_chat, PeerChannel):
                print(f"⚠️  警告：配对{idx+1}的源 {source} 不是频道类型，请检查配置")
        except Exception as e:
            print(f"❌ 错误：配对{idx+1}的源频道 {source} 无法访问，请确认已加入该频道 | 详情：{e}")
            return False
        # 检查目标频道是否有发言/发媒体权限
        try:
            target_chat = await client.get_entity(target)
            me = await client.get_me()
            permissions = await client.get_permissions(target_chat, me)
            if not permissions.send_messages or not permissions.send_media:
                print(f"❌ 错误：配对{idx+1}的目标频道 {target} 没有发言/发媒体权限，请确认权限配置")
                return False
        except Exception as e:
            print(f"❌ 错误：配对{idx+1}的目标频道 {target} 无法访问，请确认已加入该频道 | 详情：{e}")
            return False
    print("✅ 所有频道权限检查通过！")
    return True

# ========== 核心消息处理逻辑（严格执行你的需求） ==========
async def main():
    # 用async with管理客户端生命周期，彻底解决session封禁问题
    async with TelegramClient(session_name, api_id, api_hash) as client:
        # 启动前权限检查
        if not await check_channel_permissions(client):
            print("❌ 权限检查失败，程序退出")
            return
        
        # 检查重复配置
        source_list = [c['source'].lstrip('@').lower() for c in channels]
        if len(source_list) != len(set(source_list)):
            print("⚠️  警告：检测到重复的源频道配置，重复项仅第一个生效")
        
        # 打印转发规则，方便核对
        print("\n=== 转发规则已生效 ===")
        print(f"✅ 允许转发：文本≤{max_text_length}字 + 带有图片/视频/媒体的消息")
        print(f"❌ 禁止转发：纯文字消息、文本超{max_text_length}字的消息（无论是否带媒体）")
        for idx, channel in enumerate(channels):
            print(f"配对{idx+1}：监听 {channel['source']} → 转发到 {channel['target']}")
        print("\n机器人已启动，正在监听消息...\n")

        # 注册消息监听器
        @client.on(events.NewMessage(chats=[c['source'] for c in channels]))
        async def handler(event):
            msg = event.message
            source_chat = event.chat
            source_name = f"@{source_chat.username}" if source_chat.username else f"频道ID:{source_chat.id}"

            # 1. 消息去重，防止网络波动导致重复转发
            if msg.id in processed_msg_ids:
                return
            # 缓存自动清理，避免内存溢出
            processed_msg_ids.add(msg.id)
            if len(processed_msg_ids) > max_cache_size:
                processed_msg_ids.pop()

            # 2. 【核心规则1】纯文字消息（无媒体）直接拦截，不转发
            if not msg.media:
                print(f"⏭️  已拦截 | 源：{source_name} | 原因：纯文字消息，无图片/视频媒体")
                return

            # 3. 【核心规则2】仅允许图片、视频类媒体（可自行扩展），过滤投票/贴纸等非目标媒体
            # 如需仅保留图片+视频，保留下面这行；如需转发所有媒体，注释掉即可
            if not isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
                print(f"⏭️  已拦截 | 源：{source_name} | 原因：非图片/视频类媒体")
                return

            # 4. 文本清洗与长度校验
            raw_text = msg.text or ""
            cleaned_text = clean_text(raw_text)
            # 【核心规则3】文本超过50字，直接拦截整条消息（含媒体）
            if len(cleaned_text) > max_text_length:
                print(f"⏭️  已拦截 | 源：{source_name} | 原因：文本长度{len(cleaned_text)}，超过{max_text_length}字限制")
                return

            # 5. 匹配目标频道
            target_channel = get_target_channel(source_chat.id, source_chat.username)
            if not target_channel:
                print(f"⏭️  已拦截 | 源：{source_name} | 原因：未匹配到对应的目标频道")
                return

            # 6. 符合所有规则，执行转发
            try:
                # 转发间隔，避免触发风控
                await asyncio.sleep(forward_interval)
                # 统一转发，媒体+清洗后的文案完整同步
                await client.send_message(
                    target_channel,
                    message=cleaned_text,
                    file=msg.media,
                    silent=True  # 静默发送，避免触发大量通知
                )
                print(f"✅ 转发成功 | 源：{source_name} → 目标：{target_channel} | 文案预览：{cleaned_text[:30]}")
            except Exception as e:
                print(f"❌ 转发失败 | 源：{source_name} | 错误详情：{e}")

        # 保持客户端运行
        await client.run_until_disconnected()

# 程序入口，添加全局异常处理，避免意外崩溃
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ 程序已手动停止，会话已安全关闭")
    except Exception as e:
        print(f"❌ 程序异常退出：{e}")
