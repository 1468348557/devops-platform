#!/usr/bin/env sh
# 由 .vscode 里的终端配置在启动时 source（zsh (venv) profile）
# 目标效果：先激活 conda py310，再叠加项目 .venv，提示符形如 "(.venv) (py310)"

# 集成终端用 zsh -c 启动时，PATH 里往往还没有 conda，先尝试常见安装位置
for _condash in \
  "$HOME/miniconda3/etc/profile.d/conda.sh" \
  "$HOME/mambaforge/etc/profile.d/conda.sh" \
  "$HOME/anaconda3/etc/profile.d/conda.sh" \
  "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh" \
  "/usr/local/Caskroom/miniconda/base/etc/profile.d/conda.sh"
 do
  if [ -f "$_condash" ]; then
    . "$_condash"
    break
  fi
 done

# 已能在 PATH 里找到 conda 时，用官方 hook 再保险一层（zsh 子 shell 与上面对齐）
if command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.zsh hook 2>/dev/null)" 2>/dev/null || true
  conda activate py310 2>/dev/null || true
fi

[ -f .venv/bin/activate ] && source .venv/bin/activate
