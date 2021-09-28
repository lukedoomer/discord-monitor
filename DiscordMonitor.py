#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import base64
import datetime
import discord
import html
import json
import os
import platform
import signal
import sys
import threading
import time
import traceback

from aiohttp import ClientConnectorError, ClientProxyConnectionError
from plyer import notification
from pytz import timezone as tz

from Log import add_log
from PushTextProcessor import PushTextProcessor
from LinePush import LinePush

# Log file path
log_path = 'discord_monitor.log'
# Timezone
timezone = tz('Asia/Taipei')

img_MIME = ["image/png", "image/jpeg", "image/gif"]

lock = threading.Lock()


class DiscordMonitor(discord.Client):

    def __init__(self, message_user, message_channel, message_channel_name, user_dynamic_user, user_dynamic_server,
                 do_toast: bool, line_push: LinePush, push_text_processor: PushTextProcessor,
                 query_interval=60, **kwargs):
        discord.Client.__init__(self, **kwargs)

        self.message_user = message_user
        self.message_channel = message_channel
        self.message_channel_name = message_channel_name
        self.user_dynamic_user = user_dynamic_user
        self.user_dynamic_server = user_dynamic_server
        self.line_push = line_push
        self.push_text_processor = push_text_processor
        self.event_set = set()
        self.username_dict = {}
        self.nick_dict = {}
        self.interval = query_interval
        self.connect_times = 0
        if platform.system() == 'Windows' and platform.release() == '10' and do_toast:
            self.do_toast = True
        else:
            self.do_toast = False
        self.message_monitoring = True
        self.user_monitoring = True
        if 0 in self.message_channel and len(self.message_channel_name) == 0:
            self.message_monitoring = False
        if 0 in self.user_dynamic_server or len(self.user_dynamic_user) == 0:
            self.user_monitoring = False

    def is_monitored_object(self, user, channel, server, user_dynamic=False):
        """
        判断事件是否由被检测对象发出

        :param channel: 动态来源Channel
        :param user_dynamic:是否为用户动态
        :param user:动态来源用户
        :param server:动态来源Server
        :return:
        """
        # 用户动态
        if user_dynamic:
            # 被检测用户列表为空
            if len(self.user_dynamic_user) == 0:
                return False
            # 用户id在列表中 且 server在列表中或列表为空
            elif str(user.id) in self.user_dynamic_user and \
                    (server.id in self.user_dynamic_server or len(self.user_dynamic_server) == 0):
                return True
        # 消息动态
        else:
            # 被检测用户列表为空 或 用户id在列表中
            if len(self.message_user) == 0 or str(user.id) in self.message_user:
                # 被检测频道列表为空 或 频道在列表中 或 频道名称在列表中
                if (len(self.message_channel) == 0 or channel.id in self.message_channel or
                        (server.name in self.message_channel_name and channel.name in self.message_channel_name[server.name])):
                    return True
        return False

    async def process_message(self, message: discord.Message, status):
        """
        处理消息动态，并生成推送消息文本及log

        :param message: Message
        :param status: 消息动态
        :return:
        """
        content_cat = self.push_text_processor.get_content_cat(message.content)
        if not content_cat and content_cat != "":
            return
        attachment_urls = list()
        image_cqcodes = list()
        print("processing")
        for attachment in message.attachments:
            attachment_urls.append(attachment.url)
            if attachment.content_type in img_MIME:
                # 尝试利用discord.py加载图片为base64，使用代理情况下会无法连接
                #image = await attachment.read(use_cached=False)
                #image_base64 = base64.b64encode(image).decode("utf8")
                #image_cqcodes.append(f"[CQ:image,file=base64://{image_base64}==,timeout=5]")
                image_cqcodes.append(f"[CQ:image,file={attachment.url},timeout=5]")
        attachment_str = ' ; '.join(attachment_urls)
        image_str = "".join(image_cqcodes)
        content = self.push_text_processor.sub(message.content)
        if self.do_toast:
            if status == 'TAG':
                toast_title = '%s #%s %s' % (message.guild.name, message.channel.name, status)
            elif len(self.message_user) != 0:
                toast_title = '%s %s' % (self.message_user[str(message.author.id)], status)
            else:
                toast_title = '%s %s' % (message.author.name, status)
            if len(content) >= 240:
                toast_text = content[:240] + "..." if len(message.attachments) == 0 else content + "..." + "[附件]"
            else:
                toast_text = content if len(message.attachments) == 0 else content + "[附件]"
            notification.notify(toast_title, toast_text, app_icon='icon.ico', app_name='Discord Monitor')
        if len(attachment_str) > 0:
            attachment_log = '. Attachment: ' + attachment_str
        else:
            attachment_log = ''
        if status == 'POST':
            t = message.created_at.replace(tzinfo=datetime.timezone.utc).astimezone(timezone).strftime(
                '%Y/%m/%d %H:%M:%S')
        else:
            t = datetime.datetime.now(tz=timezone).strftime('%Y/%m/%d %H:%M:%S')
        log_text = '%s: ID: %d. Username: %s. Server: %s. Channel: %s. Content: %s%s' % \
                   (status, message.author.id,
                    message.author.name + '#' + message.author.discriminator,
                    message.guild.name, message.channel.name, message.content, attachment_log)
        add_log(0, 'Discord', log_text)
        keywords = {"type": status,
                    "user_id": str(message.author.id),
                    "user_name": message.author.name,
                    "user_discriminator": message.author.discriminator,
                    "channel_id:": str(message.channel.id),
                    "channel_name": message.channel.name,
                    "server_id": str(message.guild.id),
                    "server_name": message.guild.name,
                    "content": self.push_text_processor.escape_cqcode(content),
                    "content_cat": content_cat,
                    "attachment": attachment_str,
                    "image": image_str,
                    "time": t,
                    "timezone": timezone.zone}
        if len(self.message_user) != 0:
            keywords["user_display_name"] = self.message_user[str(message.author.id)]
        else:
            keywords["user_display_name"] = message.author.name + '#' + message.author.discriminator
        push_text = self.push_text_processor.push_text_process(keywords, is_user_dynamic=False)
        self.line_push.push_message(push_text, attachment_urls)

    async def process_user_update(self, before, after, user: discord.Member, status):
        """
        处理用户动态，并生成推送消息文本及log

        未指定被检测用户时应无法进入此方法

        :param before:
        :param after:
        :param user: Member或User
        :param status: 事件类型
        :return:
        """
        if self.do_toast:
            toast_title = '%s %s' % (self.user_dynamic_user[str(user.id)], status)
            toast_text = '变更后：%s' % after
            notification.notify(toast_title, toast_text[:250], app_icon='icon.ico', app_name='Discord Monitor')
        t = datetime.datetime.now(tz=timezone).strftime('%Y/%m/%d %H:%M:%S')
        log_text = '%s: ID: %d. Username: %s. Server: %s. Before: %s. After: %s.' % \
                   (status, user.id,
                    user.name + '#' + user.discriminator,
                    user.guild.name, before, after)
        add_log(0, 'Discord', log_text)
        keywords = {"type": status,
                    "user_id": user.id,
                    "user_name": user.name,
                    "user_discriminator": user.discriminator,
                    "user_display_name": self.user_dynamic_user[str(user.id)],
                    "server_id": str(user.guild.id),
                    "server_name": user.guild.name,
                    "before": before,
                    "after": after,
                    "time": t,
                    "timezone": timezone.zone}
        push_text = self.push_text_processor.push_text_process(keywords, is_user_dynamic=True)
        self.line_push.push_message(push_text, None)

    async def on_ready(self, *args, **kwargs):
        """
        完全准备好时触发，暂时用于处理大型服务器中无法接收消息的问题，随时可能被依赖库修复

        :param args:
        :param kwargs:
        :return:
        """
        if not self.user.bot:
            for guild in self.guilds:
                payload = {
                    "op": 14,
                    "d": {
                        "guild_id": str(guild.id),
                        "typing": True,
                        "threads": False,
                        "activities": True,
                        "members": [],
                        "channels": {
                            str(guild.channels[0].id): [
                                [
                                    0,
                                    99
                                ]
                            ]
                        }
                    }
                }
                asyncio.ensure_future(self.ws.send_as_json(payload), loop=self.loop)

    async def on_connect(self):
        """
        监听连接事件，每次连接会刷新所监视用户的用户名列表，使用非bot用户监视时会另外刷新昵称列表。若使用频道名监听消息则获取频道名对应ID。并启动轮询监视。重写自discord.Client

        ***眼来了***

        :return:
        """
        log_text = 'Logged in as %s, ID: %d.' % (self.user.name + '#' + self.user.discriminator, self.user.id)
        print(log_text + '\n')
        add_log(0, 'Discord', log_text)
        if self.user_monitoring:
            is_bot = self.user.bot
            for uid in self.user_dynamic_user:
                uid = int(uid)
                user = None
                for guild in self.guilds:
                    try:
                        user = await guild.fetch_member(uid)
                        if not is_bot:
                            try:
                                self.nick_dict[uid][guild.id] = user.nick
                            except:
                                self.nick_dict[uid] = {guild.id: user.nick}
                    except:
                        continue
                if user:
                    self.username_dict[uid] = [user.name, user.discriminator]
                else:
                    log_text = 'Fetch ID %s\'s username failed.' % uid
                    add_log(2, 'Discord', log_text)
        self.connect_times += 1
        if not self.user.bot and self.user_monitoring:
            await self.polling(self.connect_times)

    async def polling(self, times):
        """
        轮询监视

        :param times: 连接次数，发生变动终止本次轮询，防止重复监视
        :return:
        """
        while times == self.connect_times:
            await asyncio.sleep(self.interval)
            if not self.user.bot and self.user_monitoring:
                await self.watch_nick()

    async def watch_nick(self):
        """
        非bot用户轮询监视用户名变动及昵称变动

        :return:
        """
        for uid in self.user_dynamic_user:
            uid = int(uid)
            user = None
            for guild in self.guilds:
                try:
                    user = await guild.fetch_member(uid)
                    try:
                        self.nick_dict[uid][guild.id]
                    except KeyError:
                        try:
                            self.nick_dict[uid][guild.id] = user.nick
                        except KeyError:
                            self.nick_dict[uid] = {guild.id: user.nick}
                        continue
                    if self.nick_dict[uid][guild.id] != user.nick:
                        nick_prev = self.nick_dict[uid][guild.id]
                        self.nick_dict[uid][guild.id] = user.nick
                        await self.process_user_update(nick_prev, user.nick, user, '昵称更新')
                except:
                    continue
            if user:
                try:
                    self.username_dict[uid]
                except KeyError:
                    self.username_dict[uid] = [user.name, user.discriminator]
                    continue
                if self.username_dict[uid][0] != user.name or self.username_dict[uid][1] != user.discriminator:
                    before_screenname = self.username_dict[uid][0] + '#' + self.username_dict[uid][1]
                    after_screenname = user.name + '#' + user.discriminator
                    self.username_dict[uid][0] = user.name
                    self.username_dict[uid][1] = user.discriminator
                    await self.process_user_update(before_screenname, after_screenname, user, '用户名更新')

    async def on_disconnect(self):
        """
        监听断开连接事件，重写自discord.Client

        :return:
        """
        log_text = 'Disconnected...'
        add_log(1, 'Discord', log_text)
        print()

    async def on_message(self, message):
        """
        监听消息发送事件，重写自discord.Client

        :param message: Message
        :return:
        """
        if not self.message_monitoring:
            return
        # 消息标注事件亦会被捕获，同时其content及attachments为空，需特判排除
        if self.is_monitored_object(message.author, message.channel, message.guild) and (message.content != '' or len(message.attachments) > 0):
            await self.process_message(message, 'POST')

    async def on_message_delete(self, message):
        """
        监听消息删除事件，重写自discord.Client

        :param message: Message
        :return:
        """
        if not self.message_monitoring:
            return
        if self.is_monitored_object(message.author, message.channel, message.guild):
            await self.process_message(message, 'DELETE')

    async def on_message_edit(self, before, after):
        """
        监听消息编辑事件，重写自discord.Client

        :param before: Message
        :param after: Message
        :return:
        """
        if not self.message_monitoring:
            return
        if self.is_monitored_object(after.author, after.channel, after.guild) and before.content != after.content:
            await self.process_message(after, 'EDIT')

    async def on_guild_channel_pins_update(self, channel, last_pin):
        """
        监听频道内标注消息更新事件，重写自discord.Client

        :param channel: 频道
        :param last_pin: datetime.datetime 最新标注消息的发送时间
        :return:
        """
        if not self.message_monitoring:
            return
        if channel.id in self.message_channel or len(self.message_channel) == 0 or \
                (channel.guild.name in self.message_channel_name and channel.name in self.message_channel_name[channel.guild.name]):
            pins = await channel.pins()
            if len(pins) > 0:
                await self.process_message(pins[0], 'TAG')

    async def on_member_update(self, before, after):
        """
        监听用户状态更新事件，重写自discord.Client

        :param before: Member
        :param after: Member
        :return:
        """
        if not self.user_monitoring:
            return
        if self.is_monitored_object(before, None, before.guild, user_dynamic=True):
            # 昵称变更
            if before.nick != after.nick:
                event = str(before.nick) + str(after.nick)
                if self.check_event(event):
                    await self.process_user_update(before.nick, after.nick, before, '昵称更新')
                    self.delete_event(event)
            # 在线状态变更
            if before.status != after.status:
                event = str(before.id) + str(before.status) + str(after.status)
                if self.check_event(event):
                    await self.process_user_update(self.get_status(before.status), self.get_status(after.status),
                                                   before, '状态更新')
                    self.delete_event(event)
            # 用户名或Tag变更
            try:
                self.username_dict[before.id]
            except KeyError:
                self.username_dict[before.id] = [after.name, after.discriminator]
            if self.username_dict[before.id][0] != after.name or self.username_dict[before.id][
                1] != after.discriminator:
                before_screenname = self.username_dict[before.id][0] + '#' + self.username_dict[before.id][1]
                after_screenname = after.name + '#' + after.discriminator
                self.username_dict[before.id][0] = after.name
                self.username_dict[before.id][1] = after.discriminator
                event = before_screenname + after_screenname
                if self.check_event(event):
                    await self.process_user_update(before_screenname, after_screenname, before, '用户名更新')
                    self.delete_event(event)
            # 用户活动变更
            if before.activity != after.activity:
                if not before.activity:
                    event = after.activity.name
                    if self.check_event(event):
                        await self.process_user_update(None, after.activity.name, before, '活动更新')
                        self.delete_event(event)
                elif not after.activity:
                    event = before.activity.name
                    if self.check_event(event):
                        await self.process_user_update(before.activity.name, None, before, '活动更新')
                        self.delete_event(event)
                elif before.activity.name != after.activity.name:
                    event = before.activity.name + after.activity.name
                    if self.check_event(event):
                        await self.process_user_update(before.activity.name, after.activity.name, before, '活动更新')
                        self.delete_event(event)

    def get_status(self, status):
        return status

    def check_event(self, event):
        """
        检查该事件是否已在用户动态事件set中，不在则将事件加入set，防止眼和监测用户同在多个Server中时重复推送用户动态

        :param event: event
        :return: True if yes, otherwise False
        """
        lock.acquire()
        if event in self.event_set:
            lock.release()
            return False
        else:
            self.event_set.add(event)
            lock.release()
            return True

    def delete_event(self, event):
        """
        设置线程延时删除set中的用户动态事件

        :param event: event
        :return:
        """
        t = threading.Thread(args=(event,), target=self.delete_thread)
        t.setDaemon(True)
        t.start()

    def delete_thread(self, event):
        """
        作为线程5秒后删除set中的用户动态事件

        :param event: event
        :return:
        """
        time.sleep(5)
        lock.acquire()
        self.event_set.remove(event)
        lock.release()


def read_config(config_file):
    with open(config_file, 'r', encoding='utf8') as f:
        config_json = json.load(f)
        config_out = dict()
        config_out["token"] = config_json['token']
        config_out["bot"] = config_json['is_bot']
        config_out["linenotify_token"] = config_json['linenotify_token']
        config_out["proxy"] = config_json['proxy']
        config_out["interval"] = config_json['interval']
        config_out["toast"] = config_json['toast']
        config_out["message_user_id"] = config_json['message_monitor']['user_id']
        config_out["message_channel_id"] = config_json['message_monitor']['channel']
        channel_name_list = config_json['message_monitor']['channel_name']
        config_out["user_dynamic_user_id"] = config_json['user_dynamic_monitor']['user_id']
        config_out["user_dynamic_server"] = set(config_json['user_dynamic_monitor']['server'])
        config_out["message_channel_name"] = dict()
        for guild_ in channel_name_list:
            for i in range(1, len(guild_)):
                try:
                    config_out["message_channel_name"][guild_[0]].add(guild_[i])
                except KeyError:
                    config_out["message_channel_name"][guild_[0]] = set()
                    config_out["message_channel_name"][guild_[0]].add(guild_[i])
        config_out["content_cat_dict"] = config_json["push_text"]["category"]
        config_out["message_format"] = config_json["push_text"]["message_format"]
        config_out["user_dynamic_format"] = config_json["push_text"]["user_dynamic_format"]
        config_out["replace"] = config_json["push_text"]["replace"]
        return config_out


if __name__ == '__main__':
    config_path = 'config.json'
    try:
        config = read_config(config_path)
    except FileNotFoundError:
        print('配置文件不存在')
    except Exception:
        print('配置文件读取出错，请检查配置文件各参数是否正确')
        if platform.system() == 'Windows':
            os.system('pause')
        sys.exit(1)
    push = LinePush(config["linenotify_token"])
    push_processor = PushTextProcessor(config["message_format"], config["user_dynamic_format"], config["replace"], config["content_cat_dict"])
    intents = discord.Intents.all()
    if config["proxy"] != '':
        # 云插眼
        dc = DiscordMonitor(config["message_user_id"],
                            config["message_channel_id"],
                            config["message_channel_name"],
                            config["user_dynamic_user_id"],
                            config["user_dynamic_server"],
                            config["toast"],
                            push,
                            push_processor,
                            query_interval=config["interval"],
                            proxy=config["proxy"],
                            intents=intents)
    else:
        # 直接插眼
        dc = DiscordMonitor(config["message_user_id"],
                            config["message_channel_id"],
                            config["message_channel_name"],
                            config["user_dynamic_user_id"],
                            config["user_dynamic_server"],
                            config["toast"],
                            push,
                            push_processor,
                            query_interval=config["interval"],
                            intents=intents)
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        print('Logging in...')
        dc.run(config["token"])
    except ClientProxyConnectionError:
        print('代理错误，请检查代理设置')
    except (TimeoutError, ClientConnectorError):
        print('连接超时，请检查连接状态及代理设置')
    except discord.errors.LoginFailure:
        print('登录失败，请检查Token及bot设置是否正确，或更新Token')
    except Exception:
        print('登录失败，请检查配置文件中各参数是否正确')
        traceback.print_exc()

    if platform.system() == 'Windows':
        os.system('pause')
