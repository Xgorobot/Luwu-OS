import sys
with open('/home/pi/luwu-os/apps/sound_locate/main.py', 'r') as f:
    code = f.read()

# add corrected+cxdir to debug output
old_debug = '''                if _debug_frame % 20 == 0:
                    raw_l = _rms_db(left); raw_r = _rms_db(right)
                    print(f"{TAG} #{_debug_frame} raw L{raw_l:.0f} R{raw_r:.0f} | voice L{left_db:.0f} R{right_db:.0f} diff={left_db-right_db:+.1f} dir={xdir}", flush=True)'''

new_debug = '''                if _debug_frame % 10 == 0:
                    raw_l = _rms_db(left); raw_r = _rms_db(right)
                    diff = left_db - right_db
                    print(f"{TAG} #{_debug_frame} raw L{raw_l:.0f} R{raw_r:.0f} | voice L{left_db:.0f} R{right_db:.0f} diff={diff:+.1f}", flush=True)'''

assert old_debug in code, 'old debug not found'
code = code.replace(old_debug, new_debug)

with open('/home/pi/luwu-os/apps/sound_locate/main.py', 'w') as f:
    f.write(code)
print('done')
