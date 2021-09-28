import json
import threading
import time
import requests

from line_notify import LineNotify
from tempfile import NamedTemporaryFile

class LinePush(object):

    def __init__(self, token):
        self.notify = LineNotify(token)

    def push_message(self, message, attachments):
        t = threading.Thread(args=(message, attachments), target=self.push_thread)
        t.setDaemon(True)
        t.start()

    def push_thread(self, message, attachments):
        for attachment in attachments:
            file = NamedTemporaryFile()
            with open(file.name, "wb") as fp:
                r = requests.get(attachment)
                fp.write(r.content)
            self.notify.send(attachment, image_path=file.name)
        self.notify.send(message)
