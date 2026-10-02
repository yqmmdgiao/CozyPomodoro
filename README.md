# Cozy Pomodoro

一个基于 PySide6 的舒适风番茄钟桌面应用。

## 功能

- 可设定创作时长、休息时长、循环次数（开始/暂停/重置）
- 导入本地歌曲播放，支持启动时自动播放
- 背景图 / 背景视频切换
- 窗口任意缩放，全元素等比缩放
- 窗口置顶（Windows API，不重建窗口）
- 音频设备热切换（插拔耳机自动切换输出）
- 开始/结束/阶段切换提示音
- 计时数字双击直接编辑
- 底部音乐条鼠标悬停淡入淡出

## 运行

`ash
pip install -r requirements.txt
python main.py
`

## 资源

- ssets/icon.png — 应用图标（需自行放置）
- ssets/dialog_icon.png — 完成弹窗图标（需自行放置）
- ssets/notify.wav — 提示音（首次运行自动生成）
