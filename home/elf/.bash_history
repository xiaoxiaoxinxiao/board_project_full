mkdir -p /home/elf/slr_demo
cd /media/xiaoxin/Data/SOFTWORE/linux_xinwei/SLR_dataset_3
cd /home/elf/slr_demo
ls
python3 realtime_demo.py
sudo apt update
apt install -y python3-opencv
sudo apt install -y python3-opencv
python3 -c "import cv2; print(cv2.__version__)"
python3 -c "from rknnlite.api import RKNNLite; print('rknnlite ok')"
find / -name "*rknn*lite*.whl" 2>/dev/null
find / -name "*rknn*toolkit*lite*.whl" 2>/dev/null
sudo apt install -y python3-pip
sudo pip3 install rknn-toolkit-lite2==2.3.2 --break-system-packages
python3 -c "from rknnlite.api import RKNNLite; print('rknnlite ok')"
ls /dev/video-camera0
python3 realtime_demo.py
ls -l /dev/rknpu*
sudo chmod 666 /dev/rknpu
python3 realtime_demo.py
v4l2-ctl --list-devices
gst-launch-1.0 v4l2src device=/dev/video23 num-buffers=1 ! video/x-raw,format=NV12,width=640,height=480,framerate=30/1 ! videoconvert ! jpegenc ! filesink location=/home/elf/slr_demo/test23.jpg
ls -lh /home/elf/slr_demo/test23.jpg
rm -f /home/elf/slr_demo/test23.jpg
sudo chmod 666 /dev/video23
sudo chmod 666 /dev/video31
gst-launch-1.0 v4l2src device=/dev/video23 num-buffers=1 ! video/x-raw,format=NV12,width=640,height=480,framerate=30/1 ! mppjpegenc ! filesink location=/home/elf/slr_demo/test23.jpg
ls -lh /home/elf/slr_demo/test23.jpg
v4l2-ctl -d /dev/video23 --list-formats-ext
v4l2-ctl -d /dev/video31 --list-formats-ext
rm -f /home/elf/slr_demo/test23.jpg
gst-launch-1.0 v4l2src device=/dev/video23 io-mode=4 num-buffers=1 ! video/x-raw,format=NV12,width=640,height=480,framerate=30/1 ! mppjpegenc ! filesink location=/home/elf/slr_demo/test23.jpg
ls -lh /home/elf/slr_demo/test23.jpg
rm -f /home/elf/slr_demo/test23.jpg
sudo gst-launch-1.0 v4l2src device=/dev/video23 num-buffers=1 ! 'video/x-raw,format=NV12,width=640,height=480' ! mppjpegenc ! filesink location=/home/elf/slr_demo/test23.jpg
ls -lh /home/elf/slr_demo/test23.jpg
sudo reboot
