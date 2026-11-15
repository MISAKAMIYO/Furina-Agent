import sys, traceback
sys.path.insert(0, r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend")
try:
    import zhiyan_web.dsh
    print('init ok', hasattr(zhiyan_web.dsh, 'router'), hasattr(zhiyan_web.dsh, 'dsh_router'))
except Exception:
    traceback.print_exc()