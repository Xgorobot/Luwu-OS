with open('/home/pi/luwu-os/apps/sound_locate/main.py', 'r') as f:
    code = f.read()

# Find _on_energy and replace
old_marker = '    def _on_energy(self, left_db: float, right_db: float, xdir: int):'
old_start = code.find(old_marker)
old_end = code.find('\n    # ===', old_start)
if old_end == -1:
    old_end = code.find('\n    def paintEvent', old_start)
assert old_start >= 0 and old_end > old_start

new_method = '''    def _on_energy(self, left_db: float, right_db: float, xdir: int):
        """自适应基线：安静时缓慢追踪环境偏置，说话时冻结基线对比。"""
        self._left_db = left_db
        self._right_db = right_db
        diff = left_db - right_db

        # 初始化
        if not hasattr(self, '_baseline'):
            self._baseline = diff
            self._baseline_frames = 0

        self._baseline_frames += 1

        # 判断是否在说话：voice 能量高于 -30dB 认为有人声
        speaking = max(left_db, right_db) > -30

        if not speaking:
            # 安静环境：缓慢更新基线（EWMA, alpha=0.02）
            self._baseline = 0.02 * diff + 0.98 * self._baseline
        # 说话时 baseline 冻结

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
        spk = "\U0001f399" if speaking else "-"
        self._status.setText(
            f"{track_str} | \u7075\u654f\u5ea6:{sens_name} | {spk} diff{diff:+.1f} base{self._baseline:+.1f} corr{corrected:+.1f}"
        )
        if self._baseline_frames % 20 == 0:
            spk_label = "SPEAKING" if speaking else "silence"
            print(f"{TAG} GUI dir=\\"{self._direction}\\" {spk_label} diff{diff:+.1f} base{self._baseline:+.1f} corr{corrected:+.1f} thresh{thresh:.1f}", flush=True)'''

code = code[:old_start] + new_method + code[old_end:]

with open('/home/pi/luwu-os/apps/sound_locate/main.py', 'w') as f:
    f.write(code)
print('done')
