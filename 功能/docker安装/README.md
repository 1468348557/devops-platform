# Docker & Docker-Compose 麒麟系统离线安装

## 目录结构

```
docker安装/
├── install_docker_kylin.sh     # 主安装脚本
├── README.md                   # 本文档
└── 物料/
    ├── docker/                # 存放 docker-*.tgz 二进制包
    └── docker-compose/         # 存放 docker-compose 二进制文件
```

---

## 第一步：在有网络的机器上下载物料

在一台能访问互联网的 Linux 机器（架构需与目标麒麟机器一致）上执行：

```bash
# 方式一：直接用脚本下载（推荐，自动使用国内镜像加速）
bash install_docker_kylin.sh download

# 方式二：手动下载（国内网络推荐用阿里云镜像）
# 下载 Docker（二选一，根据架构）
wget --no-check-certificate https://mirrors.aliyun.com/docker-ce/linux/static/stable/x86_64/docker-27.5.1.tgz -O 物料/docker/docker-27.5.1.tgz
wget --no-check-certificate https://mirrors.aliyun.com/docker-ce/linux/static/stable/aarch64/docker-27.5.1.tgz -O 物料/docker/docker-27.5.1.tgz

# 下载 docker-compose（二选一，根据架构）
wget --no-check-certificate https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-x86_64 -O 物料/docker-compose/docker-compose-linux-x86_64
wget --no-check-certificate https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-aarch64 -O 物料/docker-compose/docker-compose-linux-aarch64
# 下载后记得添加执行权限
chmod +x 物料/docker-compose/docker-compose-linux-*
```

> ⚠️ 如果目标机器是 arm64（如飞腾 CPU），需要下载 aarch64 版本，否则是 x86_64 版本。

---

## 第二步：将物料拷贝到目标服务器

```bash
# 将整个 docker安装/ 目录打包
tar -czf docker-install.tar.gz docker安装/

# 拷贝到目标麒麟服务器
scp docker-install.tar.gz root@目标服务器IP:/tmp/

# 在目标服务器上解压
cd /tmp && tar -xzf docker-install.tar.gz
```

---

## 第三步：在目标服务器上执行安装

```bash
cd /tmp/docker安装
bash install_docker_kylin.sh
```

**可选参数：**
```bash
bash install_docker_kylin.sh          # 正常安装
bash install_docker_kylin.sh reinstall  # 先卸载再重新安装
```

---

## 验证安装

```bash
docker --version
docker-compose version
systemctl status docker
```

---

## 卸载 Docker

```bash
systemctl stop docker
systemctl stop containerd
rm -f /usr/bin/docker* /usr/local/bin/docker-compose
rm -rf /etc/systemd/system/docker* /etc/systemd/system/containerd*
systemctl daemon-reload
```

---

## 默认配置说明

安装后 Docker 配置文件位于 `/etc/docker/daemon.json`：

```json
{
  "registry-mirrors": [],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "storage-driver": "overlay2"
}
```

如需添加镜像加速，修改 `registry-mirrors` 字段后执行：
```bash
systemctl restart docker
```

---

## 常见问题

### 1. 提示 "未找到 Docker 二进制包"
确认 `物料/docker/` 目录下有 `docker-*.tgz` 文件，且版本号与脚本中 `DOCKER_VERSION` 一致。

### 2. Docker 启动失败
查看日志：
```bash
journalctl -u docker -n 50
```
常见原因：内核版本太低（需 ≥ 3.10），或 overlay2 存储驱动不支持（可改为 `devicemapper`）。

### 3. 麒麟 V4 系统启动报错
麒麟 V4 基于 Ubuntu 16.04，可能需要手动加载 overlay 内核模块：
```bash
modprobe overlay
```
