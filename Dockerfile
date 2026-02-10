FROM python:3.10-slim

WORKDIR /app

# Set timezone to Asia/Shanghai (common for Chinese users)
ENV TZ=Asia/Shanghai
ENV PYTHONUNBUFFERED=1
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install dependencies
COPY core/requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Copy application code
COPY . .

# Environment variables to be overridden
ENV COLLECTOR_AW_DB_PATH=/data/activitywatch.db

# Command to run the agent
CMD ["python", "core/main.py"]
