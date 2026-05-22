import sys
with open('/home/pi/luwu-os/apps/sound_locate/main.py', 'r') as f:
    code = f.read()

old_start = code.find('    def _on_energy(self')
old_end = code.find('\n    # ===', old_start)
if old_end == -1:
    old_end = code.find('\n    def paintEvent', old_start)
assert old_start >= 0 and old_end > old_start, f'start={old_start} end={old_end}'

new_method = '''    def _on_energy(self, left_db: float, right_db: float, xdir: int):
        """dong tai di xian + shun tai jian ce.

        zhui zong ren sheng pin duan cha zhi de jin qi zui xiao zhi zuo wei huan jing di xian,
        zhi you dang qian cha zhi xian zhu pian li di xian shi cai pan ding fang xiang.
        """
        self._left_db = left_db
        self._right_db = right_db
        diff = left_db - right_db

        if not hasattr(self, '_baseline'):
            self._baseline = diff
            self._baseline_frames = 0
        self._baseline_frames += 1
        if self._baseline_frames % 50 == 0:
            self._baseline = diff
        else:
            self._baseline = min(self._baseline, diff)

        corrected = diff - self._baseline
        thresh = self._threshold

        if corrected > thresh:
            self._direction = "\u2190 \u5de6"
        elif corrected < -thresh:
            self._direction = "\u2192 \u53f3"
        else:
            self._direction = ""

        if self._auto_track and not self._dog_busy:
            if corrected > thresh:
                self._do_turn(-1)
            elif corrected < -thresh:
                self._do_turn(1)

        sens_name = SENSITIVITY_PRESETS[self._sensitivity_idx][0]
        track_str = "\U0001f7e2 \u81ea\u52a8" if self._auto_track else "\u26aa \u624b\u52a8"
        self._status.setText(
            f"{track_str} | \u7075\u654f\u5ea6:{sens_name} | diff{diff:+.1f} base{self._baseline:+.1f} corr{corrected:+.1f}"
        )'''

code = code[:old_start] + new_method + code[old_end:]

with open('/home/pi/luwu-os/apps/sound_locate/main.py', 'w') as f:
    f.write(code)
print('done')
