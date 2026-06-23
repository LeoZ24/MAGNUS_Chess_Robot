```markdown
# Autonomous Chess Robot (Science Fair Project)

An autonomous, vision-guided chess-playing robotic system designed for school and science fair exhibitions. The project integrates computer vision (OpenCV & ArUco markers), magnetic self-alignment mechanisms, high-precision robotic actuation, and a chess engine to play physical chess games autonomously against a human player or another AI.

## 🚀 Project Overview
The system utilizes a top-down camera to track the state of a custom 3D-printed chessboard. Each chess piece features a specialized ArUco marker badge that allows the computer vision system to identify the piece type, color, and position. A robotic arm powered by high-precision encoder motors executes the moves calculated by the chess engine, handling piece captures and precise placement through an engineered magnetic gripper system.

## 🛠️ Technological Stack

### Core Components & Brain
* **Vision Processing:** Laptop (MacBook Air M1) / Future migration to Raspberry Pi 4/5.
* **Motion Control Platform:** CyberPi microcontroller board.
* **Actuators:** mBot2 High-Precision Encoder Motors (offering excellent torque and rotational feedback control).
* **3D Printing:** Prusa CoreOne using Black PLA filament.

### Software & Libraries
* **Language:** Python 3.x
* **Computer Vision:** OpenCV (`opencv-contrib-python`) with ArUco marker module.
* **Chess Logic:** Stockfish API / Chess engine integration wrapper.

## 📏 Design & Hardware Specifications

### 1. Chessboard & Pieces
* **Chessboard:** 32 mm square layout, 3D-printed in 4 interlocking quadrants using matte black PLA.
* **Chess Pieces:** Circular base design (22.5 mm diameter).
* **Contrast Optimization:** Because the board is completely black, each piece sticker incorporates a **compulsory white outer ring (Quiet Zone)** surrounding the square ArUco marker. This ensures maximum edge contrast for the detection algorithm.

### 2. Magnetic Self-Alignment System
To counteract minor mechanical tolerances and physical drift, a multi-tier magnetic alignment matrix is deployed:
* **Robotic Arm Gripper:** 12x3mm N52 Neodymium Magnet.
* **Chess Pieces:** 10x2mm Magnets embedded in the base.
* **Chessboard Squares:** 6x3mm Magnets embedded under the surface of each square.
* *Engineering Note:* The magnetic pull of the arm gripper is carefully balanced to securely lift pieces from the board without accidentally dragging adjacent pieces or failing to break the board-to-piece magnetic bond.

## 👁️ Computer Vision & ArUco Pipeline
The system utilizes the **4x4_50 ArUco Dictionary**. Due to the project's unique aesthetic design, the centers of the markers are overlaid with standard chess piece icons (e.g., Knights, Pawns, Queens). 

To handle center occlusion, the vision pipeline is optimized as follows:
* **Corner Prioritization:** The detection algorithm ignores the center and prioritizes the 4 outer corners of the ArUco marker.
* **Algorithmic Parameters:** Configured with an aggressive `errorCorrectionRate` (≥ 0.8), `adaptiveThreshWinSizeMin = 3`, and `adaptiveThreshWinSizeMax = 25` to handle challenging lighting environments typical of science fairs.
* **Stabilization (Lock-in Logic):** To avoid flickering and false negatives, a piece's position is only registered and "locked" into system memory after being continuously detected across 5 consecutive frames.

### Target Marker IDs Matrix
Priority tracking is given to the following specialized IDs optimized for high corner contrast: `0, 1, 2, 6, 8, 9, 12, 15, 16, 18, 21, 23`.

## 📂 Repository Structure & Testing
* `ArUco_Test.py`: Diagnostic script used to test real-time piece tracking, frame equalization, calibration resets (via the 'R' key), and inventory management visualization.
* `/assets`: Graphical representations of the high-contrast piece stickers (e.g., `Caballo Blanco.png`, `Peon Negro.png`, etc.).
