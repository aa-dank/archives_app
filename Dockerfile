FROM python:3.9-slim-buster
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

# Set the working directory to /app
WORKDIR /app

# Create user with the same UID and GID as the host user
ARG USERNAME=adankert
ARG USER_UID=1000
ARG USER_GID=$USER_UID
RUN groupadd --gid $USER_GID $USERNAME && \
    useradd --uid $USER_UID --gid $USER_GID -m $USERNAME

# Copy the current directory contents into the container at /app
COPY . /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    libpango1.0-dev \
    libcairo2-dev \
    libgirepository1.0-dev \
    pkg-config \
    smbclient \
    redis-server \
    nfs-common \
    cifs-utils

# Install any needed packages specified in requirements.txt
RUN pip install --trusted-host pypi.python.org -r requirements.txt

# Expose port 5000 for the Flask application
EXPOSE 5000

# Set the environment variable for Flask app
ENV FLASK_APP=run.py

# Set the user for the container
USER $USERNAME