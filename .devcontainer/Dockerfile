FROM mcr.microsoft.com/devcontainers/base:ubuntu

# 安装基础依赖 + Xvfb (虚拟显示器) + Playwright 系统依赖
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
        curl ca-certificates ffmpeg \
        xvfb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 以 vscode 用户安装 opencode
USER vscode
RUN curl -fsSL https://opencode.ai/install | bash
