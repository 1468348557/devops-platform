if [ -f "$HOME/.zshrc" ]; then
  source "$HOME/.zshrc"
fi

if [ -f "$PWD/.terminal-init.sh" ]; then
  source "$PWD/.terminal-init.sh"
fi
