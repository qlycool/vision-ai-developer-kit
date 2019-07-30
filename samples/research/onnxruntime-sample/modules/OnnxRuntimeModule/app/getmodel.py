# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.

import numpy as np
import onnxruntime as rt
import cv2
import time
import datetime
import sys
import json

def resize_and_pad(image, size_w, size_h, pad_value=114):
    image_h, image_w = image.shape[:2]

    is_based_w = float(size_h) >= (image_h * size_w / float(image_w))
    if  is_based_w:
        target_w = size_w
        target_h = int(np.round(image_h * size_w / float(image_w)))
    else:
        target_w = int(np.round(image_w * size_h / float(image_h)))
        target_h = size_h
        
    #image = cv2.resize(image, (target_w, target_h), 0, 0, interpolation=cv2.INTER_NEAREST)
    image = cv2.resize(image, (target_w, target_h), 0, 0, interpolation=cv2.INTER_LINEAR)

    top = int(max(0, np.round((size_h - target_h) / 2)))
    left = int(max(0, np.round((size_w - target_w) / 2)))
    bottom = size_h - top - target_h
    right = size_w - left - target_w
    image = cv2.copyMakeBorder(image, top, bottom, left, right,
                               cv2.BORDER_CONSTANT, value=[pad_value, pad_value, pad_value])

    return image

def sigmoid(x, derivative=False):
    return x*(1-x) if derivative else 1/(1+np.exp(-x))

def softmax(x):
    scoreMatExp = np.exp(np.asarray(x))
    return scoreMatExp / scoreMatExp.sum(0)

def draw_object(image, color, label, confidence, x1, y1, x2, y2, iot_hub_manager):
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    cv2.rectangle(image, (x1, y1 - 40), (x1 + 200, y1), color, -1)
    cv2.putText(image, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 1, (255 - color[0], 255 - color[1], 255 - color[2]), 1, cv2.LINE_AA)

    message = { "Label": label,
                "Confidence": "{:6.4f}".format(confidence),
                "Position": [int(x1), int(y1), int(x2), int(y2)],
                "TimeStamp": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
              }
    print('detection result: {}' .format(json.dumps(message)))
    if iot_hub_manager is not None:
        # Send message to IoT Hub                    
        iot_hub_manager.send_message_to_upstream(json.dumps(message))

def output_result(image, duration):
    if duration > 0.0:
        # Write detection time
        fps = 1.0 / duration
        text = "Detect 1 frame : {:8.6f} sec | {:6.2f} fps" .format(duration, fps)
        cv2.putText(image, text, (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 1, cv2.LINE_AA)

    # Reduce image size to speed up image saving
    image_h, image_w = image.shape[:2]
    image_h = int(image_h / 2)
    image_w = int(image_w / 2)
    image = cv2.resize(image, (image_w, image_h))
    cv2.imwrite("output/result.jpg", image)

class TinyYOLOv2Class():
    def __init__(self, iot_hub_manager = None):
        self.model_file = 'tiny_yolov2/model.onnx'
        self.threshold = 0.4
        self.numClasses = 20
        self.labels = ["aeroplane","bicycle","bird","boat","bottle",
                       "bus","car","cat","chair","cow","dining table",
                       "dog","horse","motorbike","person","potted plant",
                       "sheep","sofa","train","tv monitor"
                      ]
        self.colors = [(255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),
                       (255,255,0),(0,255,255),(255,0,255),(128,128,0),(0,128,128),(128,0,128),
                       (255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),
                       (255,64,64),(64,255,64)
                      ]
        self.anchors = [1.08, 1.19, 3.42, 4.41, 6.63, 11.38, 9.42, 5.11, 16.62, 10.52]

        self.size_w = 416
        self.size_h = 416

        self.iot_hub_manager = iot_hub_manager

        # Load model
        self.session = rt.InferenceSession(self.model_file)        
        self.inputs = self.session.get_inputs()
        for i in range(len(self.inputs)):
            print("input[{}] name = {}, type = {}" .format(i, self.inputs[i].name, self.inputs[i].type))

    def draw_bboxes(self, result, image, duration):
        out = result[0][0]
        for cy in range(0,13):
            for cx in range(0,13):
                for b in range(0,5):
                    channel = b*(self.numClasses+5)
                    tx = out[channel  ][cy][cx]
                    ty = out[channel+1][cy][cx]
                    tw = out[channel+2][cy][cx]
                    th = out[channel+3][cy][cx]
                    tc = out[channel+4][cy][cx]
                    x = (float(cx) + sigmoid(tx))*32
                    y = (float(cy) + sigmoid(ty))*32
   
                    w = np.exp(tw) * 32 * self.anchors[2*b  ]
                    h = np.exp(th) * 32 * self.anchors[2*b+1] 
   
                    confidence = sigmoid(tc)

                    classes = np.zeros(self.numClasses)
                    for c in range(0, self.numClasses):
                        classes[c] = out[channel + 5 + c][cy][cx]
                    classes = softmax(classes)
                    class_index = classes.argmax()
                
                    if (classes[class_index] * confidence < self.threshold):
                        continue

                    x = x - w/2  # left on the resized image
                    y = y - h/2  # top on the resized image

                    # draw BBOX on the original image
                    image_h, image_w = image.shape[:2]
                    is_based_w = float(self.size_h) >= (image_h * self.size_w / float(image_w))
                    if  is_based_w:
                        scale = float(image_w) / self.size_w
                        offset = (self.size_h - image_h * self.size_w / float(image_w)) / 2
                        y -= offset
                    else:
                        scale = float(image_h) / self.size_h
                        offset = (self.size_w - image_w * self.size_h / float(image_h)) / 2
                        x -= offset
                
                    x1 = max(int(np.round(x * scale)), 0)
                    y1 = max(int(np.round(y * scale)), 0)
                    x2 = min(int(np.round((x + w) * scale)), image_w)
                    y2 = min(int(np.round((y + h) * scale)), image_h)

                    # Draw labels and bbox and output message
                    draw_object(image, self.colors[class_index], self.labels[class_index], confidence, 
                                x1, y1, x2, y2, self.iot_hub_manager)

        # Output detection result
        output_result(image, duration)
        image = None

    def detect_image(self, image):
        try:
            # Preprocess input image
            image_data = image[:, :, [2, 1, 0]]  # BGR => RGB
            image_data = resize_and_pad(image_data, self.size_w, self.size_h)
            image_data = np.ascontiguousarray(np.array(image_data, dtype=np.float32).transpose(2, 0, 1)) # HWC -> CHW
            image_data = np.expand_dims(image_data, axis=0)

            # Detect image
            start_time = time.time()
            result = self.session.run(None, {self.inputs[0].name: image_data})
            end_time = time.time()
            duration = end_time - start_time  # sec
            
            # Output detection result
            self.draw_bboxes(result, image, duration)

        except Exception as ex:
            print("Exception in detect_image: %s" % ex)
            time.sleep(0.1)

        image_data = None

class YOLOV3Class():
    def __init__(self, iot_hub_manager = None):
        self.model_file = 'yolov3/yolov3.onnx'
        self.threshold = 0.5
        self.numClasses = 80
        self.labels = [ "person", "bicycle", "car", "motorbike", "aeroplane", "bus", "train", "truck", "boat", "traffic light", 
                        "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", 
                        "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", 
                        "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", 
                        "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", 
                        "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "sofa", "pottedplant", "bed", 
                        "diningtable", "toilet", "tvmonitor", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", 
                        "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
                       ]
        self.colors = [ (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64),
                        (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64),
                        (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64),
                        (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64)
                       ]
        self.anchors = [1.08, 1.19, 3.42, 4.41, 6.63, 11.38, 9.42, 5.11, 16.62, 10.52]

        self.size_w = 416
        self.size_h = 416

        self.iot_hub_manager = iot_hub_manager

        # Load model
        self.session = rt.InferenceSession(self.model_file)        
        self.inputs = self.session.get_inputs()
        for i in range(len(self.inputs)):
            print("input[{}] name = {}, type = {}" .format(i, self.inputs[i].name, self.inputs[i].type))

    def draw_bboxes(self, result, image, duration):
        out_boxes, out_scores, out_classes = result[:3]
        image_h, image_w = image.shape[:2]
        for i in range(len(out_classes)):
            batch_index, class_index, box_index = out_classes[i][:3]
            confidence = out_scores[batch_index][class_index][box_index]
            if confidence >= self.threshold:
                y1, x1, y2, x2 = out_boxes[batch_index][box_index]

                x1 = max(int(np.round(x1)), 0)
                y1 = max(int(np.round(y1)), 0)
                x2 = min(int(np.round(x2)), image_w)
                y2 = min(int(np.round(y2)), image_h)

                # Draw labels and bbox and output message
                draw_object(image, self.colors[class_index], self.labels[class_index], confidence, 
                            x1, y1, x2, y2, self.iot_hub_manager)

        # Output detection result
        output_result(image, duration)
        image = None

    def detect_image(self, image):
        try:
            # Preprocess input image
            image_data = image[:, :, [2, 1, 0]]  # BGR => RGB
            image_data = resize_and_pad(image_data, self.size_w, self.size_h)
            image_data = np.ascontiguousarray(np.array(image_data, dtype=np.float32).transpose(2, 0, 1)) # HWC -> CHW
            image_data /= 255.
            image_data = np.expand_dims(image_data, axis=0)

            image_size = np.array([image.shape[0], image.shape[1]], dtype=np.float32).reshape(1, 2)

            # Detect image
            start_time = time.time()
            result = self.session.run(None, {self.inputs[0].name: image_data, self.inputs[1].name: image_size})
            end_time = time.time()
            duration = end_time - start_time  # sec
            
            # Ouput detection result
            self.draw_bboxes(result, image, duration)

        except Exception as ex:
            print("Exception in detect_image: %s" % ex)
            time.sleep(0.1)

        image_data = None

class FasterRCNNClass():
    def __init__(self, iot_hub_manager = None):
        self.model_file = 'faster_rcnn_R_50_FPN_1x.onnx'
        self.threshold = 0.5
        self.numClasses = 81
        self.labels = [ "__background", "person", "bicycle", "car", "motorbike", "aeroplane", "bus", "train", "truck", "boat", "traffic light", 
                        "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow", 
                        "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee", 
                        "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle", 
                        "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", 
                        "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "sofa", "pottedplant", "bed", 
                        "diningtable", "toilet", "tvmonitor", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", 
                        "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
                       ]
        self.colors = [ (0,0,0), (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64),
                        (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64),
                        (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64),
                        (255,0,0),(0,255,0),(0,0,255),(128,0,0),(0,128,0),(0,0,128),(255,255,0),(0,255,255),(255,0,255),(128,128,0),
                        (0,128,128),(128,0,128),(255,128,128),(128,255,128),(128,128,255),(128,64,64),(64,128,64),(64,64,128),(255,64,64),(64,255,64)
                       ]

        # size_w and size_h need to be divisible of 32 as mentioned in 
        # https://github.com/onnx/models/tree/master/vision/object_detection_segmentation/faster-rcnn#preprocessing-steps
        self.size_w = 960
        self.size_h = 640

        self.iot_hub_manager = iot_hub_manager

        # Load model
        self.session = rt.InferenceSession(self.model_file)        
        self.inputs = self.session.get_inputs()
        for i in range(len(self.inputs)):
            print("input[{}] name = {}, type = {}" .format(i, self.inputs[i].name, self.inputs[i].type))

    def draw_bboxes(self, result, image, duration):
        out_boxes, out_classes, out_scores = result[:3]
        image_h, image_w = image.shape[:2]

        for bbox, class_index, confidence in zip(out_boxes, out_classes, out_scores):
            if confidence >= self.threshold:
                x1, y1, x2, y2 = bbox[:4]
                
                x1 = max(int(np.round(x1 * image_w / self.size_w)), 0)
                y1 = max(int(np.round(y1 * image_h / self.size_h)), 0)
                x2 = min(int(np.round(x2 * image_w / self.size_w)), image_w)
                y2 = min(int(np.round(y2 * image_h / self.size_h)), image_h)

                # Draw labels and bbox and output message
                draw_object(image, self.colors[class_index], self.labels[class_index], confidence, 
                            x1, y1, x2, y2, self.iot_hub_manager)

        # Output detection result
        output_result(image, duration)
        image = None

    def detect_image(self, image):
        try:
            # Preprocess input image
            image_data = resize_and_pad(image, self.size_w, self.size_h)
            image_data = np.ascontiguousarray(np.array(image_data, dtype=np.float32).transpose(2, 0, 1)) # HWC -> CHW

            # Normalize
            mean_vec = np.array([102.9801, 115.9465, 122.7717])
            for i in range(image_data.shape[0]):
                image_data[i, :, :] = image_data[i, :, :] - mean_vec[i]

            # Detect image
            start_time = time.time()
            result = self.session.run(None, {self.inputs[0].name: image_data})
            end_time = time.time()
            duration = end_time - start_time  # sec
            
            # Ouput detection result
            self.draw_bboxes(result, image, duration)

        except Exception as ex:
            print("Exception in detect_image: %s" % ex)
            time.sleep(0.1)

        image_data = None

class EmotionClass():
    def __init__(self, iot_hub_manager = None):
        self.model_file = 'emotion_ferplus/model.onnx'
        self.face_classifier_file = 'emotion_ferplus/haarcascade_frontalface_default.xml'
        self.threshold = 0.5
        self.numClasses = 8
        self.labels = ["neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"]
        self.colors = [(255,0,0),(0,255,0),(0,0,255),(0,255,255),(255,0,255),(255,255,0),(0,0,64),(0,64,0)]

        # size_w and size_h need to be divisible of 32 as mentioned in 
        # https://github.com/onnx/models/tree/master/vision/object_detection_segmentation/faster-rcnn#preprocessing-steps
        self.size_w = 64
        self.size_h = 64        
        self.input_shape = (1, 1, 64, 64)

        self.iot_hub_manager = iot_hub_manager

        # Load OpenCV pretrained Haar-cascade face classifier
        # https://opencv-python-tutroals.readthedocs.io/en/latest/py_tutorials/py_objdetect/py_face_detection/py_face_detection.html#haar-cascade-detection-in-opencv
        self.face_cascade = cv2.CascadeClassifier(self.face_classifier_file)

        # Load model
        self.session = rt.InferenceSession(self.model_file)        
        self.inputs = self.session.get_inputs()
        for i in range(len(self.inputs)):
            print("input[{}] name = {}, type = {}" .format(i, self.inputs[i].name, self.inputs[i].type))

    def detect_image(self, image):
        try:
            # get faces in image
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            faces = self.face_cascade.detectMultiScale(gray, 1.3, 5)

            duration = 0.0
            for (x, y, w, h) in faces:
                # Preprocess input image
                image_data = gray[y:y+h, x:x+w]
                image_data = resize_and_pad(image_data, self.size_w, self.size_h, 0)
                image_data = np.array(image_data, dtype=np.float32)
                image_data = np.resize(image_data, self.input_shape)

                # Detect image
                start_time = time.time()
                result = self.session.run(None, {self.inputs[0].name: image_data})
                end_time = time.time()
                duration = end_time - start_time  # sec

                # Postprocess output data and draw emotion label
                scores = result[0][0]
                for i in range(len(scores)):
                    scores[i] = max(scores[i], 1e-9)   # convert negative value to be 1e-9
                scores = softmax(scores)
                class_index = np.argmax(scores)
                confidence = scores[class_index]
                if confidence >= self.threshold:
                    draw_object(image, self.colors[class_index], self.labels[class_index], confidence, 
                                x, y, x + w, y + h, self.iot_hub_manager)

            # Ouput detection result
            output_result(image, duration)

        except Exception as ex:
            print("Exception in detect_image: %s" % ex)
            time.sleep(0.1)

        image_data = None
        image = None