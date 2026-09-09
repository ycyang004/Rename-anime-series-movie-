# ===== OpenList Episode Rename 生产镜像（多阶段） =====
#
# 阶段一 deps：只装依赖，可单独构建并永久复用（避免每次 pip 联网下载）：
#   docker build --target deps -t openlist-episode-renamer:base .
#   # 换镜像源：--build-arg PIP_INDEX=https://pypi.org/simple
#
# 阶段二 应用：在 deps 上直接 COPY 本地仓库文件，之后每次改代码重新构建都不再联网：
#   docker build -t openlist-episode-renamer:latest .

ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

FROM python:3.12-slim AS deps
ARG PIP_INDEX
COPY requirements.txt ./
RUN pip install --no-cache-dir --index-url "${PIP_INDEX}" -r requirements.txt

FROM deps
WORKDIR /app
ENV EPISODE_PATH=/data

COPY core.py server.py ./
COPY frontend ./frontend

VOLUME /data
EXPOSE 8000

CMD ["python", "server.py", "0.0.0.0", "8000"]