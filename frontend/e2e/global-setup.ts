import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";

/** Create a fresh synthetic pyramidal slide (unique seed -> never deduplicated) with the worker image. */
export default function globalSetup() {
  const dir = path.resolve(__dirname, ".tmp");
  mkdirSync(dir, { recursive: true });
  const seed = String(Date.now() % 100000);
  execFileSync(
    "docker",
    [
      "run",
      "--rm",
      "-v",
      `${dir}:/out`,
      process.env.WORKER_IMAGE ?? "gleasonai/worker:latest",
      "python",
      "-m",
      "prostate_infer.synthetic",
      "/out/e2e-slide.tiff",
      "--width",
      "8960",
      "--height",
      "4480",
      "--seed",
      seed,
    ],
    { stdio: "inherit" },
  );
}
