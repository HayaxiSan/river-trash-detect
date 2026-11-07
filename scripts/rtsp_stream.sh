#!/bin/bash
exec gst-launch-1.0 v4l2src device=/dev/video11 ! video/x-raw,format=UYVY,width=640,height=640 ! mpph264enc ! h264parse ! flvmux ! rtmp2sink location=rtmp://127.0.0.1:1935/live/myStream
