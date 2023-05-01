FROM python:3.9-slim-buster
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

# Set up Flask application in development mode
ENV FLASK_ENV=development
ENV FLASK_DEBUG=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    libpango1.0-dev \
    pkg-config \
    smbclient \
    nfs-common \
    cifs-utils \
    sudo \
    curl \
    iproute2 \
    iputils-ping

ENV FLASK_APP=run.py

# Set the working directory to /app
WORKDIR /app/archives_app

# Mount the Windows Server shares
RUN mkdir -p /app/Data/Archive_Data
RUN mkdir -p /app/Data/PPC_Records
RUN mkdir -p /app/Data/Cannon_Scans
RUN mkdir -p /app/archives_app

# Copy requirements.txt to /app/archives_app
COPY requirements.txt /app/archives_app

# Expose port 5000 for the Flask application
EXPOSE 5000

# Install any needed packages specified in requirements.txt
RUN /usr/local/bin/python -m pip install --upgrade pip
RUN pip install --trusted-host pypi.python.org -r /app/archives_app/requirements.txt
RUN pip install redis