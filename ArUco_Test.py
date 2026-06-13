import cv2
import cv2.aruco as aruco
import numpy as np

def detectar_aruco_fijo():
    # IDs de tu lista
    IDS_OBJETIVO = {0, 1, 2, 6, 8, 9, 12, 15, 16, 18, 21, 23}
    
    # Diccionarios para guardar la "memoria" del tablero
    posiciones_fijas = {} # Guarda {id: esquinas}
    conteo_confirmacion = {id_obj: 0 for id_obj in IDS_OBJETIVO}
    
    cap = cv2.VideoCapture(0)
    diccionario = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    parametros = aruco.DetectorParameters()
    
    # Configuración agresiva para cámaras difíciles
    parametros.adaptiveThreshWinSizeMin = 3
    parametros.adaptiveThreshWinSizeMax = 25
    parametros.errorCorrectionRate = 1.0 
    
    detector = aruco.ArucoDetector(diccionario, parametros)

    print("Buscando piezas... Presiona 'r' para resetear el tablero.")

    while True:
        ret, frame = cap.read()
        if not ret: break

        # Pre-procesamiento para ayudar a la cámara
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        esquinas_det, ids_det, _ = detector.detectMarkers(gray)

        # 1. Lógica de registro y enclavamiento
        if ids_det is not None:
            for i in range(len(ids_det)):
                id_val = int(ids_det[i][0])
                if id_val in IDS_OBJETIVO:
                    # Si aún no está fijo, aumentamos su contador de confianza
                    if id_val not in posiciones_fijas:
                        conteo_confirmacion[id_val] += 1
                        # Si se detecta 5 veces seguidas, lo fijamos
                        if conteo_confirmacion[id_val] >= 5:
                            posiciones_fijas[id_val] = esquinas_det[i]
                    else:
                        # Si ya está fijo, actualizamos la posición levemente por si hubo un micro-ajuste
                        posiciones_fijas[id_val] = esquinas_det[i]

        # 2. Dibujar TODOS los marcadores guardados en memoria (aunque no se vean ahora)
        for id_fijo, esq_fija in posiciones_fijas.items():
            c = esq_fija[0].astype(np.int32)
            # Dibujamos en VERDE FUERTE para indicar que está enclavado
            cv2.polylines(frame, [c], True, (0, 255, 0), 3)
            centerX = int(np.mean(c[:, 0]))
            centerY = int(np.mean(c[:, 1]))
            cv2.putText(frame, f"PIEZA {id_fijo}", (c[0][0], c[0][1] - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            cv2.circle(frame, (centerX, centerY), 3, (255, 255, 0), -1)

        # 3. UI de Inventario
        h, w, _ = frame.shape
        cv2.rectangle(frame, (0, h-120), (350, h), (30, 30, 30), -1)
        
        faltantes = [str(i) for i in IDS_OBJETIVO if i not in posiciones_fijas]
        txt_listos = f"Detectados: {len(posiciones_fijas)} / 12"
        txt_faltan = f"Faltan: {', '.join(faltantes)}"

        cv2.putText(frame, txt_listos, (20, h - 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(frame, txt_faltan[:50], (20, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
        cv2.putText(frame, "Presiona 'R' para recalibrar", (20, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (100, 100, 255), 1)

        cv2.imshow('Tablero de Ajedrez Lego - Fijo', frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'): break
        if key == ord('r'): # Resetear memoria si mueves la cámara
            posiciones_fijas = {}
            conteo_confirmacion = {id_obj: 0 for id_obj in IDS_OBJETIVO}

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    detectar_aruco_fijo()

print("end")