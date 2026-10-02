FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 设置时区为中国时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

ENV PATH="/usr/local/bin:${PATH}"

# Docker 和 Actions 使用相同的依赖版本
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY main.py push.py config.py cookie_store.py log_utils.py scheduler.py ./

# 启动命令
CMD ["python", "scheduler.py"]
