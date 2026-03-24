FROM mcr.microsoft.com/devcontainers/base:ubuntu

# 1. 安装必要的基础依赖
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends curl ca-certificates  ffmpeg\
    # 2. 执行安装脚本
    && curl -fsSL https://opencode.ai/install | bash \
    # 3. 清理缓存以缩小镜像体积
    && apt-get clean && rm -rf /var/lib/apt/lists/*

