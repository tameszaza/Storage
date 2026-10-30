FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
LABEL org.tamestorage.component="goodnotes-stats"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --only-binary=:all: --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY . .
USER 1000:1000
EXPOSE 8080
CMD ["python", "-B", "service.py"]
