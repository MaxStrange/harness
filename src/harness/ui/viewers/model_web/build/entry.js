// Bundled into one classic script for the harness's 3D viewer (QtWebEngine cannot import
// ES modules from file:// pages). Rebuild with build.sh next to this file.
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
window.HarnessThree = { THREE, OrbitControls, GLTFLoader, STLLoader, OBJLoader };
