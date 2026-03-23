# Dockerfile
FROM python:3.11-slim

# Cài đặt dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . .

# Lệnh chạy app (ví dụ Flask)
CMD ["python", "app.py"]