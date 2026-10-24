import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { cropColor, rampFor, sampleRamp, stageColor } from "./palette";
import { decodeChannel, type Bundle } from "./types";

export const CROP_CHANNEL = "crop";

const PLANTS_PER_CELL = 6;
const GROUND_SHADE = 0.74;
const MAX_TERRAIN_LIFT = 1.0;

/** Deterministic per-cell jitter so the planting pattern is stable across frames. */
function hash(row: number, col: number, salt: number): number {
  const value = Math.sin(row * 127.1 + col * 311.7 + salt * 74.7) * 43758.5453;
  return value - Math.floor(value);
}

/** Build one tuft of tapered blades, roughly 1 unit tall. */
function makeTuftGeometry(blades = 7): THREE.BufferGeometry {
  const positions: number[] = [];
  const indices: number[] = [];
  const normals: number[] = [];

  for (let b = 0; b < blades; b += 1) {
    const angle = (b / blades) * Math.PI * 2 + hash(b, 3, 1) * 0.9;
    const height = 0.62 + hash(b, 7, 2) * 0.38;
    const width = 0.052 + hash(b, 11, 3) * 0.02;
    const lean = (0.1 + hash(b, 13, 4) * 0.22) * (b % 2 === 0 ? 1 : -1);
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);

    const base = positions.length / 3;
    const corners: [number, number, number][] = [
      [-width, 0, 0],
      [width, 0, 0],
      [width * 0.25 + lean, height, 0.04 * height],
      [-width * 0.25 + lean, height, 0.04 * height],
    ];
    for (const [x, y, z] of corners) {
      positions.push(x * cos - z * sin, y, x * sin + z * cos);
      normals.push(0, 0.94, 0.34);
    }
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  geometry.setIndex(indices);
  // Normals stay vertical: a blade shades like the canopy it stands in, which
  // avoids the salt-and-pepper look of per-face normals on thin quads.
  return geometry;
}

function makeGroundGeometry(bundle: Bundle): THREE.BufferGeometry {
  const { rows, cols } = bundle.grid;
  const cell = bundle.grid.cell_size_m;
  const width = cols * cell;
  const depth = rows * cell;
  const elevation = bundle.grid.elevation;

  const at = (row: number, col: number): number => {
    const r = Math.min(rows - 1, Math.max(0, row));
    const c = Math.min(cols - 1, Math.max(0, col));
    return (elevation[r * cols + c] ?? 0) * MAX_TERRAIN_LIFT;
  };
  const corner = (i: number, j: number): number => {
    const values = [at(j - 1, i - 1), at(j - 1, i), at(j, i - 1), at(j, i)];
    return values.reduce((sum, value) => sum + value, 0) / 4;
  };

  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];

  for (let j = 0; j <= rows; j += 1) {
    for (let i = 0; i <= cols; i += 1) {
      positions.push(-width / 2 + i * cell, corner(i, j), -depth / 2 + j * cell);
      uvs.push(i / cols, j / rows);
    }
  }
  for (let j = 0; j < rows; j += 1) {
    for (let i = 0; i < cols; i += 1) {
      const a = j * (cols + 1) + i;
      const b = a + 1;
      const c = a + cols + 1;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

function makeGridGeometry(bundle: Bundle): THREE.BufferGeometry {
  const { rows, cols } = bundle.grid;
  const cell = bundle.grid.cell_size_m;
  const width = cols * cell;
  const depth = rows * cell;
  const elevation = bundle.grid.elevation;
  const at = (row: number, col: number): number => {
    const r = Math.min(rows - 1, Math.max(0, row));
    const c = Math.min(cols - 1, Math.max(0, col));
    return (elevation[r * cols + c] ?? 0) * MAX_TERRAIN_LIFT;
  };
  const lift = 0.012;
  const positions: number[] = [];

  const push = (i: number, j: number): void => {
    const x = -width / 2 + i * cell;
    const z = -depth / 2 + j * cell;
    const y = (at(j, i) + at(j - 1, i) + at(j, i - 1) + at(j - 1, i - 1)) / 4 + lift;
    positions.push(x, y, z);
  };

  for (let j = 0; j <= rows; j += 1) {
    for (let i = 0; i < cols; i += 1) {
      push(i, j);
      push(i + 1, j);
    }
  }
  for (let i = 0; i <= cols; i += 1) {
    for (let j = 0; j < rows; j += 1) {
      push(i, j);
      push(i, j + 1);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  return geometry;
}

function makeRain(count = 1400): THREE.Points {
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i += 1) {
    positions[i * 3] = (hash(i, 1, 11) - 0.5) * 44;
    positions[i * 3 + 1] = hash(i, 2, 12) * 18;
    positions[i * 3 + 2] = (hash(i, 3, 13) - 0.5) * 44;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const material = new THREE.PointsMaterial({
    color: 0xa9d8ea,
    size: 0.075,
    transparent: true,
    opacity: 0,
    depthWrite: false,
    sizeAttenuation: true,
  });
  return new THREE.Points(geometry, material);
}

export interface HoverInfo {
  row: number;
  col: number;
  clientX: number;
  clientY: number;
}

export class FieldScene {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly sun: THREE.DirectionalLight;
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly clock = new THREE.Clock();

  private bundle: Bundle | null = null;
  private ground: THREE.Mesh | null = null;
  private gridLines: THREE.LineSegments | null = null;
  private plants: THREE.InstancedMesh | null = null;
  private rain: THREE.Points | null = null;
  private bedrock: THREE.Mesh | null = null;
  private highlight: THREE.LineLoop | null = null;
  private texture: THREE.DataTexture | null = null;

  private channel = "lai";
  private targetValues: Float32Array = new Float32Array(0);
  private shownValues: Float32Array = new Float32Array(0);
  private targetBiomass: Float32Array = new Float32Array(0);
  private shownBiomass: Float32Array = new Float32Array(0);
  private maxBiomass = 1;
  private exaggeration = 1.8;
  private rainLevel = 0;
  private frameRain = 0;
  private dirty = true;
  private disposed = false;
  private hovered: { row: number; col: number } | null = null;
  private painting = false;
  private cropCells: Float32Array = new Float32Array(0);
  private cropPreview: Float32Array | null = null;

  onHover: ((info: HoverInfo | null) => void) | null = null;
  onPick: ((row: number, col: number) => void) | null = null;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.scene.background = new THREE.Color(0x0b1210);
    this.scene.fog = new THREE.FogExp2(0x0b1210, 0.009);

    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 400);
    this.camera.position.set(26, 26, 34);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.minDistance = 8;
    this.controls.maxDistance = 120;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.target.set(0, 0, 0);

    const sky = new THREE.HemisphereLight(0xbfd8e8, 0x2a2418, 0.55);
    this.scene.add(sky);

    this.sun = new THREE.DirectionalLight(0xffe9c4, 2.6);
    this.sun.position.set(-18, 30, 14);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.camera.near = 1;
    this.sun.shadow.camera.far = 120;
    this.sun.shadow.bias = -0.0006;
    this.scene.add(this.sun, this.sun.target);

    const fill = new THREE.DirectionalLight(0x8fb6c8, 0.28);
    fill.position.set(20, 14, -16);
    this.scene.add(fill);

    window.addEventListener("resize", this.resize);
    canvas.addEventListener("pointermove", this.handlePointerMove);
    canvas.addEventListener("pointerleave", this.handlePointerLeave);
    canvas.addEventListener("pointerdown", this.handlePointerDown);
    window.addEventListener("pointerup", this.handlePointerUp);
    this.resize();
    this.renderer.setAnimationLoop(this.animate);
  }

  /** Replace the rendered run and rebuild every geometry that depends on it. */
  load(bundle: Bundle, channel: string): void {
    this.bundle = bundle;
    this.channel = channel;
    this.clearField();

    const { rows, cols } = bundle.grid;
    const cell = bundle.grid.cell_size_m;
    const width = cols * cell;
    const depth = rows * cell;
    const cells = rows * cols;
    const span = Math.max(width, depth);

    const pixels = new Uint8Array(cells * 4).fill(255);
    this.texture = new THREE.DataTexture(pixels, cols, rows, THREE.RGBAFormat);
    // The bytes are sRGB; without this the shader treats them as linear and the
    // ground washes out next to the (correctly converted) plant instances.
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.magFilter = THREE.LinearFilter;
    this.texture.minFilter = THREE.LinearFilter;
    this.texture.needsUpdate = true;

    const groundMaterial = new THREE.MeshStandardMaterial({
      map: this.texture,
      roughness: 0.94,
      metalness: 0.02,
    });
    this.ground = new THREE.Mesh(makeGroundGeometry(bundle), groundMaterial);
    this.ground.receiveShadow = true;
    this.ground.name = "ground";
    this.scene.add(this.ground);

    this.gridLines = new THREE.LineSegments(
      makeGridGeometry(bundle),
      new THREE.LineBasicMaterial({
        color: 0x9ec7a4,
        transparent: true,
        opacity: 0.11,
        depthWrite: false,
      }),
    );
    this.scene.add(this.gridLines);

    const tuft = makeTuftGeometry();
    const plantMaterial = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      roughness: 0.78,
      metalness: 0.0,
      side: THREE.DoubleSide,
      vertexColors: false,
      flatShading: false,
    });
    const count = cells * PLANTS_PER_CELL;
    this.plants = new THREE.InstancedMesh(tuft, plantMaterial, count);
    this.plants.castShadow = true;
    this.plants.receiveShadow = false;
    this.plants.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.plants.name = "plants";
    const white = new THREE.Color(1, 1, 1);
    for (let i = 0; i < count; i += 1) this.plants.setColorAt(i, white);
    this.scene.add(this.plants);

    const bedrock = new THREE.Mesh(
      new THREE.PlaneGeometry(span * 7, span * 7),
      new THREE.MeshStandardMaterial({ color: 0x1b241b, roughness: 1, metalness: 0 }),
    );
    bedrock.rotation.x = -Math.PI / 2;
    bedrock.position.y = Math.min(...bundle.grid.elevation, 0) - 0.6;
    bedrock.receiveShadow = true;
    this.scene.add(bedrock);
    this.bedrock = bedrock;

    this.rain = makeRain();
    this.scene.add(this.rain);

    const ring = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-0.5, 0, -0.5),
      new THREE.Vector3(0.5, 0, -0.5),
      new THREE.Vector3(0.5, 0, 0.5),
      new THREE.Vector3(-0.5, 0, 0.5),
    ]);
    this.highlight = new THREE.LineLoop(
      ring,
      new THREE.LineBasicMaterial({ color: 0xe8f3c8, transparent: true, opacity: 0.85 }),
    );
    this.highlight.visible = false;
    this.scene.add(this.highlight);

    this.maxBiomass = Math.max(1, ...bundle.series.map((point) => point.biomass));
    this.targetValues = new Float32Array(cells);
    this.shownValues = new Float32Array(cells);
    this.targetBiomass = new Float32Array(cells);
    this.shownBiomass = new Float32Array(cells);
    this.cropCells = new Float32Array(cells);
    if (bundle.grid.crop_index && bundle.grid.crop_index.length === cells) {
      this.cropCells = Float32Array.from(bundle.grid.crop_index);
    }

    this.sun.target.position.set(0, 0, 0);
    this.sun.position.set(-span * 0.6, span * 0.95, span * 0.45);
    const shadowCamera = this.sun.shadow.camera;
    shadowCamera.left = -span;
    shadowCamera.right = span;
    shadowCamera.top = span;
    shadowCamera.bottom = -span;
    shadowCamera.updateProjectionMatrix();

    this.camera.position.set(width * 0.46, span * 0.76, depth * 0.98);
    this.controls.target.set(0, 0, 0);
    this.controls.maxDistance = span * 3.2;
    this.controls.update();

    this.dirty = true;
  }

  setChannel(channel: string): void {
    this.channel = channel;
    this.dirty = true;
  }

  /** Replace what the crop channel colours by — before the run comes back.
   *
   * Painting a cell has to be visible immediately, otherwise the brush feels
   * like it did nothing until the next simulation finishes.  ``null`` falls
   * back to the layout the bundle actually ran with.
   */
  setCropPreview(cells: number[] | null): void {
    this.cropPreview = cells && cells.length ? Float32Array.from(cells) : null;
    this.dirty = true;
  }

  /** Painting instead of orbiting: left-drag keeps working once rotate is off. */
  setPaintEnabled(enabled: boolean): void {
    this.controls.enableRotate = !enabled;
  }

  /** Apply one frame of the bundle (index into `bundle.frames`). */
  setFrame(index: number): void {
    const bundle = this.bundle;
    if (!bundle) return;
    const frame = bundle.frames[Math.min(bundle.frames.length - 1, Math.max(0, index))];
    if (!frame) return;

    const meta = bundle.channels[this.channel];
    const payload = frame.ch[this.channel];
    if (meta && payload) {
      decodeChannel(payload, meta.domain, this.targetValues);
    }
    const cropIndex = this.cropPreview ?? bundle.grid.crop_index;
    if (cropIndex && cropIndex.length === this.targetValues.length) {
      this.cropCells = Float32Array.from(cropIndex);
    } else if (this.cropCells.length !== this.targetValues.length) {
      this.cropCells = new Float32Array(this.targetValues.length);
    }
    const biomass = bundle.channels.biomass;
    const biomassPayload = frame.ch.biomass;
    if (biomass && biomassPayload) {
      decodeChannel(biomassPayload, biomass.domain, this.targetBiomass);
    }

    this.frameRain = frame.rain;
    this.dirty = true;
  }

  setExaggeration(value: number): void {
    this.exaggeration = value;
    this.dirty = true;
  }

  resetView(): void {
    if (!this.bundle) return;
    const cell = this.bundle.grid.cell_size_m;
    const width = this.bundle.grid.cols * cell;
    const depth = this.bundle.grid.rows * cell;
    const span = Math.max(width, depth);
    this.camera.position.set(width * 0.46, span * 0.76, depth * 0.98);
    this.controls.target.set(0, 0, 0);
    this.controls.update();
  }

  dispose(): void {
    this.disposed = true;
    this.renderer.setAnimationLoop(null);
    window.removeEventListener("resize", this.resize);
    this.canvas.removeEventListener("pointermove", this.handlePointerMove);
    this.canvas.removeEventListener("pointerleave", this.handlePointerLeave);
    this.canvas.removeEventListener("pointerdown", this.handlePointerDown);
    window.removeEventListener("pointerup", this.handlePointerUp);
    this.clearField();
    this.renderer.dispose();
  }

  private clearField(): void {
    for (const object of [
      this.ground,
      this.gridLines,
      this.plants,
      this.rain,
      this.highlight,
      this.bedrock,
    ]) {
      if (object) this.scene.remove(object);
    }
    this.ground?.geometry.dispose();
    this.gridLines?.geometry.dispose();
    this.plants?.geometry.dispose();
    this.plants?.dispose();
    this.rain?.geometry.dispose();
    this.texture?.dispose();
    this.ground = null;
    this.gridLines = null;
    this.plants = null;
    this.rain = null;
    this.highlight = null;
    this.bedrock = null;
    this.texture = null;
  }

  private readonly resize = (): void => {
    const width = this.canvas.clientWidth || window.innerWidth;
    const height = this.canvas.clientHeight || window.innerHeight;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  };

  private readonly handlePointerMove = (event: PointerEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    const cell = this.pickCell();
    this.hovered = cell;
    if (cell && this.highlight) {
      const grid = this.bundle?.grid;
      if (grid) {
        const index = cell.row * grid.cols + cell.col;
        const elevation = (grid.elevation[index] ?? 0) * MAX_TERRAIN_LIFT;
        this.highlight.position.set(
          -grid.cols * grid.cell_size_m * 0.5 + (cell.col + 0.5) * grid.cell_size_m,
          elevation + 0.02,
          -grid.rows * grid.cell_size_m * 0.5 + (cell.row + 0.5) * grid.cell_size_m,
        );
        this.highlight.scale.setScalar(grid.cell_size_m * 0.92);
        this.highlight.visible = true;
      }
    } else if (this.highlight) {
      this.highlight.visible = false;
    }
    this.onHover?.(cell ? { ...cell, clientX: event.clientX, clientY: event.clientY } : null);
    this.canvas.style.cursor = cell ? "crosshair" : "grab";
    if (cell && this.painting) this.onPick?.(cell.row, cell.col);
  };

  private readonly handlePointerLeave = (): void => {
    this.hovered = null;
    if (this.highlight) this.highlight.visible = false;
    this.onHover?.(null);
  };

  private readonly handlePointerDown = (event: PointerEvent): void => {
    // Orbit is disabled while painting, so a drag can paint a whole row without
    // also swinging the camera; a plain click works either way.
    if (event.button === 0) this.painting = true;
    if (this.hovered) this.onPick?.(this.hovered.row, this.hovered.col);
  };

  private readonly handlePointerUp = (): void => {
    this.painting = false;
  };

  private pickCell(): { row: number; col: number } | null {
    if (!this.ground || !this.bundle) return null;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObject(this.ground, false);
    const hit = hits[0];
    if (!hit?.uv) return null;
    const { rows, cols } = this.bundle.grid;
    const col = Math.min(cols - 1, Math.max(0, Math.floor(hit.uv.x * cols)));
    const row = Math.min(rows - 1, Math.max(0, Math.floor(hit.uv.y * rows)));
    return { row, col };
  }

  private readonly animate = (): void => {
    if (this.disposed) return;
    const delta = this.clock.getDelta();
    this.controls.update();

    if (this.bundle) {
      this.updatePlants();
      this.updateRain(delta);
    }
    this.renderer.render(this.scene, this.camera);
  };

  private updatePlants(): void {
    const bundle = this.bundle;
    if (!bundle || !this.plants || !this.texture) return;

    const { rows, cols } = bundle.grid;
    const cell = bundle.grid.cell_size_m;
    const cells = rows * cols;
    const width = cols * cell;
    const depth = rows * cell;
    const meta = bundle.channels[this.channel];
    const stageMode = this.channel === "stage";
    const cropMode = this.channel === CROP_CHANNEL;
    const ramp = rampFor(this.channel);
    const [low, high] = meta?.domain ?? [0, 1];
    const span = high - low || 1;

    // Smooth the transition between days so playback reads as growth, not cuts.
    let moving = this.dirty;
    const ease = 0.28;
    for (let i = 0; i < cells; i += 1) {
      const dv = this.targetValues[i] - this.shownValues[i];
      const db = this.targetBiomass[i] - this.shownBiomass[i];
      if (Math.abs(dv) > 1e-4 || Math.abs(db) > 1e-4) moving = true;
      this.shownValues[i] += dv * ease;
      this.shownBiomass[i] += db * ease;
    }
    if (!moving) return;
    this.dirty = false;

    const pixels = this.texture.image.data as Uint8Array;
    const matrix = new THREE.Matrix4();
    const quaternion = new THREE.Quaternion();
    const position = new THREE.Vector3();
    const scale = new THREE.Vector3();
    const color = new THREE.Color();
    const cropScale = bundle.meta.crop === "maize" ? 2.1 : 1.0;

    for (let index = 0; index < cells; index += 1) {
      const row = Math.floor(index / cols);
      const col = index % cols;
      const value = cropMode ? (this.cropCells[index] ?? 0) : this.shownValues[index];
      const t = (value - low) / span;
      const [r, g, b] = cropMode
        ? cropColor(value)
        : stageMode
          ? stageColor(value)
          : sampleRamp(ramp, t);
      color.setRGB(r, g, b, THREE.SRGBColorSpace);

      pixels[index * 4] = Math.round(r * GROUND_SHADE * 255);
      pixels[index * 4 + 1] = Math.round(g * GROUND_SHADE * 255);
      pixels[index * 4 + 2] = Math.round(b * GROUND_SHADE * 255);
      pixels[index * 4 + 3] = 255;

      const growth = Math.max(0, Math.min(1, this.shownBiomass[index] / this.maxBiomass));
      const height = (0.18 + 0.82 * growth) * 0.66 * cropScale * this.exaggeration;
      const elevation = (bundle.grid.elevation[index] ?? 0) * MAX_TERRAIN_LIFT;

      for (let k = 0; k < PLANTS_PER_CELL; k += 1) {
        const jitterX = (hash(row, col, k * 3 + 1) - 0.5) * cell * 0.62;
        const jitterZ = (hash(row, col, k * 3 + 2) - 0.5) * cell * 0.62;
        const spin = hash(row, col, k * 3 + 3) * Math.PI * 2;
        position.set(
          -width / 2 + (col + 0.5) * cell + jitterX,
          elevation,
          -depth / 2 + (row + 0.5) * cell + jitterZ,
        );
        quaternion.setFromAxisAngle(new THREE.Vector3(0, 1, 0), spin);
        const breadth = cell * (0.44 + 0.14 * hash(row, col, k + 7));
        scale.set(breadth, height * (0.82 + 0.36 * hash(row, col, k + 11)), breadth);
        matrix.compose(position, quaternion, scale);
        const instance = index * PLANTS_PER_CELL + k;
        this.plants.setMatrixAt(instance, matrix);
        this.plants.setColorAt(instance, color);
      }
    }

    this.plants.instanceMatrix.needsUpdate = true;
    if (this.plants.instanceColor) this.plants.instanceColor.needsUpdate = true;
    this.texture.needsUpdate = true;
  }

  private updateRain(delta: number): void {
    if (!this.rain) return;
    const material = this.rain.material as THREE.PointsMaterial;
    this.rainLevel += ((this.frameRain > 0.2 ? 0.55 : 0) - this.rainLevel) * 0.08;
    material.opacity = this.rainLevel;
    if (this.rainLevel < 0.02) return;

    const positions = this.rain.geometry.getAttribute("position") as THREE.BufferAttribute;
    const array = positions.array as Float32Array;
    const fall = 9 * delta * (1 + this.frameRain * 0.08);
    for (let i = 1; i < array.length; i += 3) {
      array[i] -= fall;
      if (array[i] < -1) array[i] = 17;
    }
    positions.needsUpdate = true;
  }
}
