"""临时脚本：用新的 path_to_qq_file 直接连接 NapCat 验证全媒体发送。

测试：image / record / video / file(上传)，均走修正后的路径转换。
"""
import asyncio
import sys

sys.path.insert(0, r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend")

from zhiyan_web.platform.onebot_client import OneBotClient, path_to_qq_file  # noqa: E402

TARGET = "228117384"
VIDEO = r"d:\Python\NEW_start\智言\NR8AVjViQ1FBMU9EVTJNRGcxTnpSTDM3UnBqTm4uRXchIQUAcXVuZ3o!.mp4"
WAV = r"d:\Python\NEW_start\ZHIYAN AGENT WEB\data\tts\ZhiYan_tts_gpt_sovits_2cb4a515.wav"
IMG = r"D:\Python\NEW_start\ZHIYAN AGENT WEB\data\Pics\0000DnDg44ptTQ.jpg"
TXT = r"D:\Python\NEW_start\ZHIYAN AGENT WEB\data\workspace\test\README.md"


async def main() -> None:
    client = OneBotClient(ws_url="ws://127.0.0.1:3001", access_token="li090919")
    await client.start()
    await asyncio.sleep(2)
    try:
        for name, f in (("video", VIDEO), ("record", WAV), ("image", IMG)):
            ref = path_to_qq_file(f)
            print(f"[{name}] file -> {ref}")
            seg = [{"type": name, "data": {"file": ref}}]
            resp = await client.send_private_msg(TARGET, seg)
            print(f"  => {(resp or {}).get('status')} {(resp or {}).get('message', '')[:80]}")

        # 普通文件走 upload_private_file（NapCat 的 name 为必填）
        ref_txt = path_to_qq_file(TXT)
        print(f"[file] file -> {ref_txt}")
        resp = await client.upload_private_file(TARGET, ref_txt, name="README.md", timeout=60)
        print(f"  => {(resp or {}).get('status')} {(resp or {}).get('message', '')[:80]}")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
