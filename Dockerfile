# Use lightweight Python base image
FROM python:3.10-slim


# Set working directory
WORKDIR /app

# Copy dependency requirements
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Expose port (Google Cloud Run overrides this dynamically using PORT env)
EXPOSE 5005

# Start application using gunicorn production server
CMD ["sh", "-c", "gunicorn app:app --workers 3 --bind 0.0.0.0:${PORT:-8080} --timeout 60 --chdir src"]
