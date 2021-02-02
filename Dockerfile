# syntax=docker/dockerfile:experimental
FROM alpine:3.10

RUN apk add --no-cache python3 uwsgi-python3
RUN apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev py3-pip gcc \
    && pip3 install --upgrade pip \
    && pip3 install cython \
    && pip3 install --no-binary falcon falcon \
    && apk del .build-deps

RUN mkdir /home/rajendra
WORKDIR /home/rajendra
COPY requirements_docker.txt /home/rajendra

RUN --mount=type=ssh \
    apk add --no-cache --virtual .build-deps openssh-client git gcc musl-dev python3-dev py3-pip linux-headers cmake extra-cmake-modules build-base abuild binutils openssl-dev\
    && mkdir -p -m 0600 ~/.ssh \
    && ssh-keyscan github.com >> ~/.ssh/known_hosts \
    && pip3 install wheel \
    && pip3 install -r requirements_docker.txt \
    && apk del .build-deps

COPY tracktor /home/rajendra/src

EXPOSE 8080
ENTRYPOINT ["uwsgi", "--ini", "./src/uwsgi.ini"]
HEALTHCHECK CMD curl -f 127.0.0.1:8080/health || exit 1
