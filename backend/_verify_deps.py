import sys, asyncio
sys.path.insert(0, r'd:\Python\NEW_start\ZHIYAN AGENT WEB\backend')
from zhiyan_web.star.dependency_installer import DependencyInstaller as DI

async def main():
    # 验证 pip 通道 + returncode 判定（已安装的包会返回 0）
    ok, out = await DI._pip_install('six')
    print('pip install six ->', ok)
    print('output tail:', (out or '').strip()[-200:])

asyncio.run(main())
print('DONE')