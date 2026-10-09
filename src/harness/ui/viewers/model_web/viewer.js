// The 3D model viewer page. Python sends a model (name, extension, base64 bytes); we parse
// it, fit it to the view and report its size back. Viewing only: no editing, no options.
"use strict";

const { THREE, OrbitControls, GLTFLoader, STLLoader, OBJLoader } = window.HarnessThree;
const params = new URLSearchParams(location.search);
const colors = JSON.parse(params.get("theme") || "{}");
for (const [key, value] of Object.entries(colors)) {
  document.documentElement.style.setProperty("--" + key, value);
}

const container = document.getElementById("view");
const message = document.getElementById("message");
// Without WebGL (a broken GPU driver, some remote desktops) the page must still connect to
// Python and say so; otherwise the panel would wait on "Loading..." forever.
let renderer = null;
let graphicsError = null;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(new THREE.Color(colors.bg || "#2b3137"));
  container.appendChild(renderer.domElement);
} catch (error) {
  graphicsError = "3D graphics (WebGL) are not available here: " + (error.message || error);
  message.textContent = graphicsError;
}

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
// Soft light from everywhere plus a light that rides with the camera: whatever side you
// look at is lit, so there is nothing to adjust.
scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.6));
const headlight = new THREE.DirectionalLight(0xffffff, 1.8);
headlight.position.set(0.5, 1, 1);
camera.add(headlight);
scene.add(camera);

// OrbitControls: left drag rotates; left drag with Shift (or Ctrl), or right drag, pans;
// the wheel zooms towards the cursor.
const controls = new OrbitControls(camera, renderer ? renderer.domElement : container);
controls.enableDamping = true;
controls.dampingFactor = 0.12;
controls.zoomToCursor = true;
controls.screenSpacePanning = true;

let model = null;
let home = null; // camera position and target for "reset view"

function resize() {
  if (!renderer) return;
  const w = container.clientWidth || 1;
  const h = container.clientHeight || 1;
  renderer.setSize(w, h, false);
  renderer.domElement.style.width = w + "px";
  renderer.domElement.style.height = h + "px";
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

function animate() {
  requestAnimationFrame(animate);
  if (!renderer) return;
  controls.update();
  renderer.render(scene, camera);
}
animate();

const plain = () => new THREE.MeshStandardMaterial({
  color: 0xb8c0c8, metalness: 0.1, roughness: 0.65, side: THREE.DoubleSide,
});

function decode(b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return bytes.buffer;
}

function parse(ext, buffer) {
  return new Promise((resolve, reject) => {
    if (ext === "stl") {
      const geometry = new STLLoader().parse(buffer);
      geometry.computeVertexNormals();
      const mesh = new THREE.Mesh(geometry, plain());
      mesh.rotation.x = -Math.PI / 2; // CAD exports are Z-up; the viewer is Y-up
      resolve(mesh);
    } else if (ext === "obj") {
      const group = new OBJLoader().parse(new TextDecoder().decode(buffer));
      group.traverse((child) => {
        if (child.isMesh) {
          const m = child.material;
          const bare = !m || (Array.isArray(m) ? false : !m.map && m.color && m.color.getHex() === 0xffffff);
          if (bare) child.material = plain();
          if (child.material) child.material.side = THREE.DoubleSide;
        }
      });
      resolve(group);
    } else if (ext === "glb" || ext === "gltf") {
      const data = ext === "gltf" ? new TextDecoder().decode(buffer) : buffer;
      new GLTFLoader().parse(data, "", (gltf) => {
        gltf.scene.traverse((child) => {
          if (child.isMesh && !child.material) child.material = plain();
        });
        resolve(gltf.scene);
      }, (error) => reject(new Error(
        (error && error.message ? error.message : String(error)) +
        (ext === "gltf" ? "\n(a .gltf whose buffers are separate files cannot be opened; use .glb)" : "")
      )));
    } else {
      reject(new Error("unsupported format: ." + ext));
    }
  });
}

// Drop the momentum left from the last drag (damping) before jumping the camera. update()
// applies what is left once more and only then clears it, so this must come first, or the
// view drifts away from where it was put.
function stopMomentum() {
  controls.enableDamping = false;
  controls.update();
  controls.enableDamping = true;
}

function fit() {
  stopMomentum();
  const box = new THREE.Box3().setFromObject(model);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const radius = sphere.radius || 1;
  // The whole bounding sphere fits, so nothing clips however the model is turned.
  const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.02;
  const direction = new THREE.Vector3(1, 0.75, 1.25).normalize();
  camera.near = radius / 100;
  camera.far = radius * 100;
  camera.updateProjectionMatrix();
  camera.position.copy(sphere.center).addScaledVector(direction, distance);
  controls.target.copy(sphere.center);
  controls.minDistance = radius / 50;
  controls.maxDistance = radius * 20;
  controls.update();
  home = { position: camera.position.clone(), target: controls.target.clone() };
  return box;
}

function describe(box) {
  const size = box.getSize(new THREE.Vector3());
  let triangles = 0;
  model.traverse((child) => {
    if (child.isMesh && child.geometry) {
      const g = child.geometry;
      triangles += g.index ? g.index.count / 3 : g.attributes.position.count / 3;
    }
  });
  const n = (v) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3));
  return `${n(size.x)} x ${n(size.y)} x ${n(size.z)} (file units), ${Math.round(triangles).toLocaleString()} triangles`;
}

function clear() {
  if (!model) return;
  scene.remove(model);
  model.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    const mats = Array.isArray(child.material) ? child.material : [child.material];
    for (const m of mats) if (m && m.dispose) m.dispose();
  });
  model = null;
}

new QWebChannel(qt.webChannelTransport, (channel) => {
  const bridge = channel.objects.viewer;
  bridge.load.connect(async (name, ext, b64) => {
    if (graphicsError) {
      bridge.failed(graphicsError);
      return;
    }
    clear();
    message.textContent = "Loading " + name + "...";
    message.style.display = "flex";
    try {
      model = await parse(ext.toLowerCase(), decode(b64));
      scene.add(model);
      const info = describe(fit());
      message.style.display = "none";
      bridge.loaded(info);
    } catch (error) {
      clear();
      message.textContent = "Cannot show " + name + "\n" + error.message;
      bridge.failed(String(error.message || error));
    }
  });
  bridge.reset.connect(() => {
    if (!home) return;
    stopMomentum();
    camera.position.copy(home.position);
    controls.target.copy(home.target);
    controls.update();
  });
  bridge.ready();
});
