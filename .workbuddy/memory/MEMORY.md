
## Docker 离线安装脚本
- 路径：`/Users/wuhao/Desktop/devops-platform/功能/docker安装/`
- 文件：
  - `install_docker_kylin.sh` — 主脚本，支持 `bash install_docker_kylin.sh download` 下载物料、`bash install_docker_kylin.sh install` 安装
  - `README.md` — 使用说明
  - `物料/docker/` — 放 docker-*.tgz
  - `物料/docker-compose/` — 放 docker-compose 二进制
- 默认版本：Docker 27.5.1，docker-compose 2.32.4
- 支持架构：x86_64 / aarch64（麒麟 arm 机器）
- 安装前需先在有网机器上用 `download` 命令下载物料
