from typing import Union, Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import secrets
import string
from datetime import datetime, timedelta
from pydantic import BaseModel
from database import get_db, create_tables, QRCode, RegistroEscaneo
from sqlalchemy import desc
import httpx
import asyncio
import traceback

# Importación condicional de qrcode
try:
    import qrcode
    from io import BytesIO
    import base64
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

# URL del backend NestJS
NESTJS_BACKEND_URL = "https://backtofastapi-production.up.railway.app"

# "Buzón" en memoria para guardar el último evento de escaneo por ID de empleado.
last_scan_events: Dict[int, Dict[str, Any]] = {}

app = FastAPI(
    title="QR Attendance API - Integrado con NestJS",
    description="""
    ## 🎯 API para Control de Asistencia con Códigos QR - Integrada con Backend de Usuarios

    Sistema integrado que consume el backend de NestJS para validar empleados antes de generar códigos QR.
    """,
    version="2.3.0",
    contact={
        "name": "Sistema de Asistencia QR Integrado",
        "email": "admin@empresa.com",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
    openapi_tags=[
        {
            "name": "QR Codes",
            "description": "Operaciones para generar y validar códigos QR con validación de empleados",
        },
        {
            "name": "Employees",
            "description": "Consulta de información de empleados desde backend NestJS",
        },
        {
            "name": "Scanning",
            "description": "Registro de escaneos (entrada y salida)",
        },
        {
            "name": "Administration",
            "description": "Endpoints administrativos para gestión",
        },
        {
            "name": "Reports",
            "description": "Reportes y estadísticas de asistencia con datos de empleados",
        },
        {
            "name": "Legacy",
            "description": "Endpoints para compatibilidad con scanner existente",
        },
        {
            "name": "System",
            "description": "Información del sistema y estadísticas generales",
        }
    ]
)

# ============= CONFIGURACIÓN CORS =============
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "https://tu-frontend-domain.com", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print("🚀 Iniciando aplicación integrada...")
create_tables()

# ============= MODELOS PYDANTIC ACTUALIZADOS =============

class EmployeeInfo(BaseModel):
    id: int
    name: str
    email: str
    role: str
    # CAMPOS AÑADIDOS para coincidir con el frontend de Angular
    isActive: bool = True
    createdAt: str = ""
    firstName: Optional[str] = None
    lastName: Optional[str] = None

# NUEVO: Modelo para el registro de asistencia diario
class UserAttendanceRecord(BaseModel):
    hora_entrada: Optional[str] = None
    hora_salida: Optional[str] = None
    duracion_jornada: Optional[str] = None
    status: str  # "Present", "Absent", "Completed"

# NUEVO: Modelo combinado para la respuesta del nuevo endpoint
class UserWithAttendance(EmployeeInfo):
    attendance_today: UserAttendanceRecord

class QRGenerationRequest(BaseModel):
    empleado_id: int

# NUEVO: Modelo para regenerar QR al hacer login
class QRLoginRequest(BaseModel):
    empleado_id: int

class QRCodeResponse(BaseModel):
    id: int
    empleado_id: int
    empleado_info: Optional[EmployeeInfo] = None
    qr_code_base64: str
    creado_en: str
    activo: bool
    total_escaneos: int
    is_new: bool = False  # Indica si es un QR nuevo generado

class EscaneoResponse(BaseModel):
    id: int
    qr_id: int
    empleado_id: int
    empleado_info: Optional[EmployeeInfo] = None
    fecha: str
    hora_entrada: str
    hora_salida: Optional[str] = None
    es_entrada: bool
    duracion_jornada: Optional[str] = None

class ValidationResponse(BaseModel):
    valid: bool
    message: str
    qr_data: Optional[dict] = None
    empleado_info: Optional[EmployeeInfo] = None
    accion: Optional[str] = None

class AttendanceStatsResponse(BaseModel):
    total_qrs: int
    total_escaneos: int
    empleados_registrados: int
    escaneos_hoy: int
    backend_status: str

class ScanNotificationResponse(BaseModel):
    message: str
    type: str
    empleado_name: str
    timestamp: str

# ============= FUNCIONES PARA CONSUMIR BACKEND NESTJS (ACTUALIZADAS) =============

async def get_employee_by_id(empleado_id: int) -> Optional[EmployeeInfo]:
    """Obtiene información del empleado desde el backend NestJS"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{NESTJS_BACKEND_URL}/user/{empleado_id}", timeout=10.0)
            if response.status_code == 200:
                user_data = response.json()
                # Compatibilidad con campos de frontend
                full_name = user_data.get("name", "")
                first_name = user_data.get("firstName") or (full_name.split(" ")[0] if " " in full_name else full_name)
                last_name = user_data.get("lastName") or (" ".join(full_name.split(" ")[1:]) if " " in full_name else "")

                return EmployeeInfo(
                    id=user_data["id"],
                    name=full_name,
                    firstName=first_name,
                    lastName=last_name,
                    email=user_data["email"],
                    role=user_data["role"],
                    isActive=user_data.get("isActive", True),
                    createdAt=user_data.get("createdAt", datetime.utcnow().isoformat())
                )
            return None
    except Exception as e:
        print(f"❌ Error de conexión obteniendo empleado {empleado_id}: {e}")
        return None

async def get_all_employees() -> List[EmployeeInfo]:
    """Obtiene todos los empleados desde el backend NestJS"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{NESTJS_BACKEND_URL}/user", timeout=15.0)
            if response.status_code == 200:
                users_data = response.json()
                employee_list = []
                for user in users_data:
                    full_name = user.get("name", "")
                    first_name = user.get("firstName") or (full_name.split(" ")[0] if " " in full_name else full_name)
                    last_name = user.get("lastName") or (" ".join(full_name.split(" ")[1:]) if " " in full_name else "")
                    employee_list.append(EmployeeInfo(
                        id=user["id"],
                        name=full_name,
                        firstName=first_name,
                        lastName=last_name,
                        email=user["email"],
                        role=user["role"],
                        isActive=user.get("isActive", True),
                        createdAt=user.get("createdAt", datetime.utcnow().isoformat())
                    ))
                return employee_list
            return []
    except Exception as e:
        print(f"❌ Error de conexión obteniendo empleados: {e}")
        return []

async def check_backend_status() -> str:
    """Verifica el estado del backend NestJS"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{NESTJS_BACKEND_URL}/user",
                timeout=5.0
            )

            if response.status_code == 200:
                return "CONNECTED"
            else:
                return f"ERROR_{response.status_code}"

    except httpx.TimeoutException:
        return "TIMEOUT"
    except Exception as e:
        return f"OFFLINE"

# ============= FUNCIONES AUXILIARES =============

def generate_unique_id(length=16):
    """Genera un ID único para identificar el QR"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_qr_code(qr_id: str) -> str:
    """Genera código QR en base64"""
    if not QR_AVAILABLE:
        # Si no está disponible qrcode, generar un placeholder
        return f"QR_PLACEHOLDER_ID:{qr_id}"

    try:
        # El QR contendrá el ID del registro en la BD
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(str(qr_id))
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Error generando QR: {e}")
        return f"QR_ERROR_ID:{qr_id}"

async def qr_to_response(qr_code: QRCode, db: Session, is_new: bool = False) -> QRCodeResponse:
    """Convierte un QR code de la DB a respuesta con información del empleado"""
    total_escaneos = db.query(RegistroEscaneo).filter(RegistroEscaneo.qr_id == qr_code.id).count()

    # Obtener información del empleado
    empleado_info = await get_employee_by_id(qr_code.empleado_id)

    return QRCodeResponse(
        id=qr_code.id,
        empleado_id=qr_code.empleado_id,
        empleado_info=empleado_info,
        qr_code_base64=qr_code.qr_code_base64,
        creado_en=qr_code.creado_en.isoformat(),
        activo=qr_code.activo,
        total_escaneos=total_escaneos,
        is_new=is_new
    )

async def escaneo_to_response(escaneo: RegistroEscaneo, db: Session) -> EscaneoResponse:
    """Convierte un registro de escaneo a respuesta con información del empleado"""
    # Calcular duración si hay hora de salida
    duracion_jornada = None
    if escaneo.hora_salida:
        duracion = escaneo.hora_salida - escaneo.hora_entrada
        horas = int(duracion.total_seconds() // 3600)
        minutos = int((duracion.total_seconds() % 3600) // 60)
        duracion_jornada = f"{horas}h {minutos}m"

    # Determinar si es entrada (cuando se crea) o salida (cuando se actualiza)
    es_entrada = escaneo.hora_salida is None

    # Obtener información del empleado
    empleado_info = await get_employee_by_id(escaneo.empleado_id)

    return EscaneoResponse(
        id=escaneo.id,
        qr_id=escaneo.qr_id,
        empleado_id=escaneo.empleado_id,
        empleado_info=empleado_info,
        fecha=escaneo.fecha.date().isoformat(),
        hora_entrada=escaneo.hora_entrada.isoformat(),
        hora_salida=escaneo.hora_salida.isoformat() if escaneo.hora_salida else None,
        es_entrada=es_entrada,
        duracion_jornada=duracion_jornada
    )

# ============= FUNCIONES PARA REGENERAR QR =============

async def regenerate_qr_for_employee(empleado_id: int, db: Session) -> QRCodeResponse:
    """
    Regenera un nuevo código QR para un empleado:
    1. Desactiva el QR anterior si existe
    2. Crea un nuevo QR activo
    3. Mantiene el historial de escaneos del QR anterior
    """
    print(f"🔄 Regenerando QR para empleado {empleado_id}")
    
    # Verificar que el empleado existe
    employee = await get_employee_by_id(empleado_id)
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Empleado con ID {empleado_id} no encontrado en el sistema"
        )

    # Desactivar QR anterior si existe (no eliminar para mantener historial)
    existing_qr = db.query(QRCode).filter(
        QRCode.empleado_id == empleado_id,
        QRCode.activo == True
    ).first()

    if existing_qr:
        print(f"🔒 Desactivando QR anterior (ID: {existing_qr.id}) para empleado {empleado_id}")
        existing_qr.activo = False
        db.commit()

    # Crear nuevo QR
    print(f"🆕 Creando nuevo QR para empleado {empleado_id}")
    new_qr = QRCode(
        empleado_id=empleado_id,
        qr_code_base64="temp",  # Temporal
        activo=True
    )

    db.add(new_qr)
    db.commit()
    db.refresh(new_qr)

    # Generar el código QR usando el nuevo ID
    qr_code_base64 = generate_qr_code(new_qr.id)
    new_qr.qr_code_base64 = qr_code_base64
    db.commit()
    db.refresh(new_qr)

    print(f"✅ Nuevo QR generado exitosamente para {employee.name} (ID: {new_qr.id})")
    return await qr_to_response(new_qr, db, is_new=True)

# ============= ENDPOINTS =============

@app.get("/", tags=["System"])
async def read_root():
    backend_status = await check_backend_status()
    return {
        "Hello": "QR Attendance API - Integrado con NestJS",
        "version": "2.3.0",
        "swagger_docs": "/docs",
        "redoc_docs": "/redoc",
        "backend_nestjs": {
            "url": NESTJS_BACKEND_URL,
            "status": backend_status
        },
        "features": [
            "Generación de códigos QR por empleado validado",
            "Integración con backend NestJS para datos de empleados",
            "Registro de escaneos con información completa",
            "Control de asistencia con validación de usuarios",
            "NUEVO: Regeneración automática de QR en cada login"
        ]
    }

# ============= ENDPOINTS DE EMPLEADOS =============

@app.get("/employees", response_model=List[EmployeeInfo], tags=["Employees"])
async def get_employees():
    """📋 Obtiene todos los empleados desde el backend NestJS"""
    employees = await get_all_employees()
    return employees

@app.get("/employees/{empleado_id}", response_model=EmployeeInfo, tags=["Employees"])
async def get_employee(empleado_id: int):
    """👤 Obtiene información de un empleado específico desde el backend NestJS"""
    employee = await get_employee_by_id(empleado_id)

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Empleado con ID {empleado_id} no encontrado en el sistema"
        )

    return employee

@app.get("/employees/{empleado_id}/qr", response_model=Optional[QRCodeResponse], tags=["Employees"])
async def get_employee_qr(empleado_id: int, db: Session = Depends(get_db)):
    """🔍 Obtiene el QR código activo de un empleado específico si existe"""
    try:
        print(f"🔍 Procesando solicitud de QR para empleado {empleado_id}")

        # Verificar que el empleado existe
        print(f"🔍 Verificando existencia del empleado {empleado_id}")
        employee = await get_employee_by_id(empleado_id)
        if not employee:
            print(f"❌ Empleado {empleado_id} no encontrado en backend NestJS")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Empleado con ID {empleado_id} no encontrado en el sistema"
            )

        print(f"✅ Empleado encontrado: {employee.name}")

        # Buscar QR activo existente
        print(f"🔍 Buscando QR activo para empleado {empleado_id}")
        existing_qr = db.query(QRCode).filter(
            QRCode.empleado_id == empleado_id,
            QRCode.activo == True
        ).first()

        if existing_qr:
            print(f"✅ QR activo encontrado: ID {existing_qr.id}")
            return await qr_to_response(existing_qr, db)
        else:
            print(f"⚠️ No se encontró QR activo para empleado {empleado_id}")
            return None

    except HTTPException:
        # Re-lanzar HTTPExceptions
        raise
    except Exception as e:
        print(f"❌ Error interno en get_employee_qr: {e}")
        print(f"🔍 Traceback completo: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno del servidor: {str(e)}"
        )

# ============= NUEVO ENDPOINT PARA LOGIN/REGENERAR QR =============

@app.post("/qr/login", response_model=QRCodeResponse, tags=["QR Codes"])
async def generate_qr_on_login(request: QRLoginRequest, db: Session = Depends(get_db)):
    """
    ## 🔄 Regenera código QR al hacer login (NUEVO)
    
    Este endpoint se debe llamar cada vez que un empleado se loguea en la aplicación.
    Automáticamente:
    1. Desactiva el QR anterior del empleado
    2. Genera un nuevo QR único
    3. Mantiene el historial de escaneos anteriores
    """
    print(f"🔑 Login detectado para empleado {request.empleado_id}, regenerando QR...")
    
    # Regenerar QR para el empleado
    new_qr = await regenerate_qr_for_employee(request.empleado_id, db)
    
    print(f"✅ QR regenerado exitosamente en login para empleado {request.empleado_id}")
    return new_qr

# ============= ENDPOINTS DE QR CODES INTEGRADOS (MODIFICADOS) =============

@app.post("/qr/generate", response_model=QRCodeResponse, tags=["QR Codes"])
async def generate_qr(request: QRGenerationRequest, db: Session = Depends(get_db)):
    """
    ## 🎯 Genera un nuevo código QR para un empleado (con validación en NestJS)
    
    NOTA: Para regenerar QR en login, usar el endpoint /qr/login
    """

    # PASO 1: Validar que el empleado existe en el backend NestJS
    print(f"🔍 Validando empleado {request.empleado_id} en backend NestJS...")
    employee = await get_employee_by_id(request.empleado_id)

    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Empleado con ID {request.empleado_id} no encontrado en el sistema. Verifique que el empleado existe en el backend NestJS."
        )

    print(f"✅ Empleado encontrado: {employee.name} ({employee.email})")

    # PASO 2: Verificar si ya existe un QR activo para este empleado
    existing_qr = db.query(QRCode).filter(
        QRCode.empleado_id == request.empleado_id,
        QRCode.activo == True
    ).first()

    if existing_qr:
        print(f"📋 QR activo existente encontrado para empleado {request.empleado_id}")
        # Devolver el QR existente con información actualizada del empleado
        return await qr_to_response(existing_qr, db)

    # PASO 3: Crear nuevo QR en la base de datos
    print(f"🆕 Creando nuevo QR para empleado {request.empleado_id}...")
    db_qr = QRCode(
        empleado_id=request.empleado_id,
        qr_code_base64="temp"  # Temporal
    )

    db.add(db_qr)
    db.commit()
    db.refresh(db_qr)

    # PASO 4: Generar el código QR usando el ID de la base de datos
    qr_code_base64 = generate_qr_code(db_qr.id)

    # PASO 5: Actualizar con el QR generado
    db_qr.qr_code_base64 = qr_code_base64
    db.commit()
    db.refresh(db_qr)

    print(f"✅ QR generado exitosamente para {employee.name}")
    return await qr_to_response(db_qr, db, is_new=True)

@app.get("/qr/{qr_id}/validate", response_model=ValidationResponse, tags=["QR Codes"])
async def validate_qr(qr_id: int, db: Session = Depends(get_db)):
    """
    ## ✅ Valida un código QR y determina la próxima acción (con info del empleado)
    """

    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()

    if not qr_code:
        return ValidationResponse(
            valid=False,
            message="Código QR no encontrado"
        )

    if not qr_code.activo:
        # Intentar obtener info del empleado aunque el QR esté desactivado
        employee = await get_employee_by_id(qr_code.empleado_id)
        return ValidationResponse(
            valid=False,
            message="Código QR desactivado - Posiblemente se generó uno nuevo",
            empleado_info=employee,
            qr_data={
                "empleado_id": qr_code.empleado_id,
                "activo": False
            }
        )

    # Validar que el empleado aún existe en el backend
    employee = await get_employee_by_id(qr_code.empleado_id)
    if not employee:
        return ValidationResponse(
            valid=False,
            message=f"Empleado con ID {qr_code.empleado_id} ya no existe en el sistema"
        )

    # Verificar si hay un registro de entrada sin salida para hoy
    hoy = datetime.utcnow().date()
    registro_hoy = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.qr_id == qr_id,
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time())
    ).first()

    if registro_hoy:
        if registro_hoy.hora_salida is None:
            # Ya tiene entrada, el próximo escaneo será salida
            accion = "SALIDA"
            mensaje = f"Registrará salida de {employee.name} - Entrada: {registro_hoy.hora_entrada.strftime('%H:%M:%S')}"
        else:
            # Ya completó entrada y salida hoy
            accion = "COMPLETADO"
            mensaje = f"{employee.name} ya registró entrada y salida hoy"
    else:
        # No hay registro hoy, será entrada
        accion = "ENTRADA"
        mensaje = f"Registrará entrada de {employee.name}"

    return ValidationResponse(
        valid=True,
        message=mensaje,
        accion=accion,
        empleado_info=employee,
        qr_data={
            "empleado_id": qr_code.empleado_id,
            "activo": qr_code.activo,
            "creado_en": qr_code.creado_en.isoformat()
        }
    )

@app.post("/qr/{qr_id}/scan", response_model=EscaneoResponse, tags=["Scanning"])
async def record_scan(qr_id: int, db: Session = Depends(get_db)):
    """
    ## 📱 Registra un escaneo del código QR (entrada o salida) con validación de empleado
    - Modificado para guardar el evento de escaneo en un "buzón" en memoria para notificaciones.
    - **NUEVO**: Impide registrar la salida si no ha pasado al menos 1 minuto desde la entrada.
    """

    # Verificar que el QR existe y está activo
    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()

    if not qr_code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Código QR no encontrado"
        )

    if not qr_code.activo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código QR desactivado - Es posible que se haya generado uno nuevo para este empleado"
        )

    # Validar que el empleado aún existe en el backend
    employee = await get_employee_by_id(qr_code.empleado_id)
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Empleado con ID {qr_code.empleado_id} ya no existe en el sistema"
        )

    ahora = datetime.utcnow()
    hoy = ahora.date()

    # Buscar registro de hoy
    registro_hoy = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.qr_id == qr_id,
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time())
    ).first()

    scan_type = ""
    response_model_obj = None

    if registro_hoy:
        if registro_hoy.hora_salida is None:
            # ---- INICIO DE LA MODIFICACIÓN ----
            # Validar que ha pasado al menos 1 minuto desde la entrada
            tiempo_desde_entrada = ahora - registro_hoy.hora_entrada
            if tiempo_desde_entrada.total_seconds() < 60:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Debe esperar al menos 1 minuto después de la entrada para poder registrar la salida."
                )
            # ---- FIN DE LA MODIFICACIÓN ----

            # Registrar salida
            print(f"🚪 Registrando SALIDA para {employee.name}")
            registro_hoy.hora_salida = ahora
            db.commit()
            db.refresh(registro_hoy)
            scan_type = "SALIDA"
            response_model_obj = await escaneo_to_response(registro_hoy, db)
        else:
            # Ya completó entrada y salida
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{employee.name} ya registró entrada y salida para hoy"
            )
    else:
        # Crear nuevo registro de entrada
        print(f"🏃 Registrando ENTRADA para {employee.name}")
        nuevo_registro = RegistroEscaneo(
            qr_id=qr_id,
            empleado_id=qr_code.empleado_id,
            fecha=ahora,
            hora_entrada=ahora,
            hora_salida=None
        )

        db.add(nuevo_registro)
        db.commit()
        db.refresh(nuevo_registro)
        scan_type = "ENTRADA"
        response_model_obj = await escaneo_to_response(nuevo_registro, db)

    # Después de un escaneo exitoso, guardamos el evento en el diccionario
    if scan_type and employee:
        global last_scan_events
        
        # Obtenemos todos los administradores para notificarles
        all_users = await get_all_employees()
        admin_users = [user for user in all_users if user.role == 'ADMIN']
        
        message = f"{employee.name} ha registrado su {scan_type.lower()}."
        
        # Preparamos la notificación para cada administrador
        for admin in admin_users:
            last_scan_events[admin.id] = {
                "message": message,
                "type": scan_type,
                "empleado_name": employee.name,
                "timestamp": ahora.isoformat()
            }
            print(f"📬 Notificación preparada para admin {admin.id} ({admin.name}): {message}")

    return response_model_obj

# ============= ENDPOINT DE NOTIFICACIONES HTTP =============
@app.get("/events/last-scan/{user_id}", response_model=Optional[ScanNotificationResponse], tags=["System"])
async def get_last_scan_event(user_id: int):
    """
    ## 📬 Endpoint de polling para notificaciones de escaneo.

    El frontend llama a este endpoint cada pocos segundos.
    Si hay una notificación pendiente para el usuario, la devuelve y la elimina
    para no volver a enviarla.
    """
    global last_scan_events

    # .pop() obtiene el valor y lo elimina del diccionario atómicamente.
    event = last_scan_events.pop(user_id, None)

    if event:
        print(f"📤 Enviando notificación a usuario {user_id}: {event['message']}")
        return event

    # Si no hay evento, FastAPI devolverá un cuerpo de respuesta `null`.
    return None


# ============= ENDPOINTS ADMINISTRATIVOS MEJORADOS =============

@app.get("/admin/qrs", response_model=List[QRCodeResponse], tags=["Administration"])
async def get_all_qrs(
    empleado_id: Optional[int] = None,
    activo: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """📋 Obtiene todos los códigos QR con información de empleados"""
    query = db.query(QRCode)

    if empleado_id:
        query = query.filter(QRCode.empleado_id == empleado_id)

    if activo is not None:
        query = query.filter(QRCode.activo == activo)

    qrs = query.offset(offset).limit(limit).all()

    # Convertir a respuestas con información de empleados
    results = []
    for qr in qrs:
        qr_response = await qr_to_response(qr, db)
        results.append(qr_response)

    return results

@app.get("/admin/escaneos", response_model=List[EscaneoResponse], tags=["Administration"])
async def get_all_scans(
    qr_id: Optional[int] = None,
    empleado_id: Optional[int] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    solo_sin_salida: Optional[bool] = False,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """📊 Obtiene todos los registros de escaneo con información de empleados"""
    query = db.query(RegistroEscaneo)

    if qr_id:
        query = query.filter(RegistroEscaneo.qr_id == qr_id)

    if empleado_id:
        query = query.filter(RegistroEscaneo.empleado_id == empleado_id)

    if fecha_desde:
        try:
            fecha_desde_dt = datetime.fromisoformat(fecha_desde)
            query = query.filter(RegistroEscaneo.fecha >= fecha_desde_dt)
        except ValueError:
            pass

    if fecha_hasta:
        try:
            fecha_hasta_dt = datetime.fromisoformat(fecha_hasta)
            query = query.filter(RegistroEscaneo.fecha <= fecha_hasta_dt)
        except ValueError:
            pass

    if solo_sin_salida:
        query = query.filter(RegistroEscaneo.hora_salida.is_(None))

    escaneos = query.order_by(desc(RegistroEscaneo.fecha)).offset(offset).limit(limit).all()

    # Convertir a respuestas con información de empleados
    results = []
    for escaneo in escaneos:
        escaneo_response = await escaneo_to_response(escaneo, db)
        results.append(escaneo_response)

    return results

@app.get("/admin/empleado/{empleado_id}/escaneos", response_model=List[EscaneoResponse], tags=["Administration"])
async def get_employee_scans(empleado_id: int, db: Session = Depends(get_db)):
    """📋 Obtiene el historial completo de escaneos de un empleado específico con validación"""

    # Validar que el empleado existe
    employee = await get_employee_by_id(empleado_id)
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Empleado con ID {empleado_id} no encontrado en el sistema"
        )

    escaneos = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.empleado_id == empleado_id
    ).order_by(desc(RegistroEscaneo.fecha)).all()

    # Convertir a respuestas con información del empleado
    results = []
    for escaneo in escaneos:
        escaneo_response = await escaneo_to_response(escaneo, db)
        results.append(escaneo_response)

    return results

@app.get("/admin/reporte-diario/{fecha}", tags=["Reports"])
async def daily_report(fecha: str, db: Session = Depends(get_db)):
    """
    ## 📊 Genera reporte diario completo de asistencia con datos de empleados
    """
    try:
        fecha_obj = datetime.fromisoformat(fecha).date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de fecha inválido. Use YYYY-MM-DD"
        )

    # Obtener todos los registros del día
    registros = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.fecha >= datetime.combine(fecha_obj, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(fecha_obj, datetime.max.time())
    ).all()

    # Estadísticas
    total_empleados = len(set(r.empleado_id for r in registros))
    con_entrada = len(registros)
    con_salida = len([r for r in registros if r.hora_salida])
    sin_salida = con_entrada - con_salida

    # Detalle por empleado con información desde NestJS
    empleados_detalle = []
    for registro in registros:
        # Obtener información del empleado
        employee = await get_employee_by_id(registro.empleado_id)

        duracion = None
        if registro.hora_salida:
            diff = registro.hora_salida - registro.hora_entrada
            horas = int(diff.total_seconds() // 3600)
            minutos = int((diff.total_seconds() % 3600) // 60)
            duracion = f"{horas}h {minutos}m"

        empleado_detail = {
            "empleado_id": registro.empleado_id,
            "empleado_info": {
                "name": employee.name if employee else f"Empleado {registro.empleado_id}",
                "email": employee.email if employee else "No encontrado",
                "role": employee.role if employee else "No encontrado"
            } if employee else None,
            "hora_entrada": registro.hora_entrada.strftime("%H:%M:%S"),
            "hora_salida": registro.hora_salida.strftime("%H:%M:%S") if registro.hora_salida else None,
            "duracion_jornada": duracion,
            "completo": registro.hora_salida is not None,
            "empleado_existe": employee is not None
        }
        empleados_detalle.append(empleado_detail)

    return {
        "fecha": fecha,
        "estadisticas": {
            "total_empleados": total_empleados,
            "con_entrada": con_entrada,
            "con_salida": con_salida,
            "sin_salida": sin_salida
        },
        "empleados": empleados_detalle,
        "backend_status": await check_backend_status()
    }

@app.get("/admin/empleados/sin-salida", tags=["Reports"])
async def get_employees_without_exit(db: Session = Depends(get_db)):
    """⚠️ Obtiene empleados que registraron entrada pero no salida hoy con información completa"""
    hoy = datetime.utcnow().date()

    registros_sin_salida = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time()),
        RegistroEscaneo.hora_salida.is_(None)
    ).all()

    empleados_info = []
    for registro in registros_sin_salida:
        # Obtener información del empleado desde NestJS
        employee = await get_employee_by_id(registro.empleado_id)

        tiempo_transcurrido = datetime.utcnow() - registro.hora_entrada
        horas = int(tiempo_transcurrido.total_seconds() // 3600)
        minutos = int((tiempo_transcurrido.total_seconds() % 3600) // 60)

        empleado_info = {
            "empleado_id": registro.empleado_id,
            "empleado_info": {
                "name": employee.name if employee else f"Empleado {registro.empleado_id}",
                "email": employee.email if employee else "No encontrado",
                "role": employee.role if employee else "No encontrado"
            } if employee else None,
            "hora_entrada": registro.hora_entrada.isoformat(),
            "tiempo_transcurrido": f"{horas}h {minutos}m",
            "empleado_existe": employee is not None
        }
        empleados_info.append(empleado_info)

    return {
        "total": len(empleados_info),
        "empleados": empleados_info,
        "backend_status": await check_backend_status()
    }

@app.post("/admin/registro/{registro_id}/forzar-salida", tags=["Administration"])
async def force_exit(registro_id: int, db: Session = Depends(get_db)):
    """🚪 Fuerza una salida para un registro específico (uso administrativo) con validación"""
    registro = db.query(RegistroEscaneo).filter(RegistroEscaneo.id == registro_id).first()

    if not registro:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Registro no encontrado"
        )

    if registro.hora_salida:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este registro ya tiene hora de salida"
        )

    # Validar que el empleado aún existe
    employee = await get_employee_by_id(registro.empleado_id)

    registro.hora_salida = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "message": f"Salida forzada registrada para {employee.name if employee else f'Empleado {registro.empleado_id}'}",
        "registro": await escaneo_to_response(registro, db),
        "empleado_existe": employee is not None
    }

@app.put("/admin/qr/{qr_id}/toggle", tags=["Administration"])
async def toggle_qr_status(qr_id: int, db: Session = Depends(get_db)):
    """🔄 Activa o desactiva un código QR con información del empleado"""

    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()

    if not qr_code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Código QR no encontrado"
        )

    # Obtener información del empleado
    employee = await get_employee_by_id(qr_code.empleado_id)

    qr_code.activo = not qr_code.activo
    db.commit()

    return {
        "success": True,
        "message": f"QR {'activado' if qr_code.activo else 'desactivado'} para {employee.name if employee else f'Empleado {qr_code.empleado_id}'}",
        "qr_id": qr_id,
        "activo": qr_code.activo,
        "empleado_info": employee,
        "empleado_existe": employee is not None
    }

@app.delete("/admin/qr/{qr_id}", tags=["Administration"])
async def delete_qr(qr_id: int, db: Session = Depends(get_db)):
    """🗑️ Elimina un código QR y todos sus registros de escaneo con validación"""

    qr_code = db.query(QRCode).filter(QRCode.id == qr_id).first()

    if not qr_code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Código QR no encontrado"
        )

    # Obtener información del empleado antes de eliminar
    employee = await get_employee_by_id(qr_code.empleado_id)

    # Eliminar escaneos asociados
    escaneos_eliminados = db.query(RegistroEscaneo).filter(RegistroEscaneo.qr_id == qr_id).delete()

    # Eliminar QR
    db.delete(qr_code)
    db.commit()

    return {
        "success": True,
        "message": f"QR eliminado para {employee.name if employee else f'Empleado {qr_code.empleado_id}'} junto con {escaneos_eliminados} escaneos",
        "qr_id": qr_id,
        "escaneos_eliminados": escaneos_eliminados,
        "empleado_info": employee,
        "empleado_existe": employee is not None
    }

# ============= ESTADÍSTICAS MEJORADAS =============

@app.get("/stats", response_model=AttendanceStatsResponse, tags=["System"])
async def get_attendance_stats(db: Session = Depends(get_db)):
    """📈 Obtiene estadísticas generales del sistema de asistencia integrado"""

    # Contar totales
    total_qrs = db.query(QRCode).count()
    total_escaneos = db.query(RegistroEscaneo).count()

    # Empleados únicos con QR
    empleados_registrados = db.query(QRCode.empleado_id).distinct().count()

    # Escaneos de hoy
    hoy = datetime.utcnow().date()
    escaneos_hoy = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time())
    ).count()

    # Estado del backend
    backend_status = await check_backend_status()

    return AttendanceStatsResponse(
        total_qrs=total_qrs,
        total_escaneos=total_escaneos,
        empleados_registrados=empleados_registrados,
        escaneos_hoy=escaneos_hoy,
        backend_status=backend_status
    )

@app.get("/info", tags=["System"])
async def get_system_info(db: Session = Depends(get_db)):
    """ℹ️ Información completa del sistema integrado con estadísticas detalladas"""
    stats = await get_attendance_stats(db)

    # Obtener algunos empleados de muestra
    sample_employees = await get_all_employees()
    total_employees_backend = len(sample_employees)

    return {
        "app": "QR Attendance API - Integrado con NestJS",
        "version": "2.3.0",
        "database": "PostgreSQL (Neon) + NestJS Backend",
        "qr_available": QR_AVAILABLE,
        "backend_integration": {
            "nestjs_url": NESTJS_BACKEND_URL,
            "status": stats.backend_status,
            "total_employees_in_backend": total_employees_backend
        },
        "attendance_stats": {
            "total_qrs": stats.total_qrs,
            "total_escaneos": stats.total_escaneos,
            "empleados_registrados": stats.empleados_registrados,
            "escaneos_hoy": stats.escaneos_hoy
        },
        "features": [
            "Generación de QR por empleado validado en NestJS",
            "Integración completa con backend de usuarios",
            "Registro de múltiples escaneos con datos de empleados",
            "Control de asistencia con validación en tiempo real",
            "Reportes enriquecidos con información completa",
            "NUEVO: Regeneración automática de QR en cada login"
        ]
    }

# ============= NUEVO ENDPOINT PARA EL DASHBOARD DE USUARIOS =============

@app.get("/users/with-attendance", response_model=List[UserWithAttendance], tags=["Reports"])
async def get_users_with_attendance_today(db: Session = Depends(get_db)):
    """
    ## 📋 Obtiene todos los empleados con su registro de asistencia de hoy.
    Combina la información de empleados del backend NestJS con los registros de
    entrada/salida de la base de datos local para el día actual. Ideal para dashboards.
    """
    all_employees = await get_all_employees()
    if not all_employees:
        return []

    response_list = []
    hoy = datetime.utcnow().date()

    registros_hoy = db.query(RegistroEscaneo).filter(
        RegistroEscaneo.fecha >= datetime.combine(hoy, datetime.min.time()),
        RegistroEscaneo.fecha < datetime.combine(hoy, datetime.max.time())
    ).all()

    registros_dict = {registro.empleado_id: registro for registro in registros_hoy}

    for employee in all_employees:
        registro_hoy = registros_dict.get(employee.id)

        attendance_record = UserAttendanceRecord(status="Absent")

        if registro_hoy:
            hora_entrada_str = registro_hoy.hora_entrada.strftime("%H:%M:%S") if registro_hoy.hora_entrada else None
            hora_salida_str = registro_hoy.hora_salida.strftime("%H:%M:%S") if registro_hoy.hora_salida else None
            duracion_str = None
            status = "Present"

            if registro_hoy.hora_salida:
                duracion = registro_hoy.hora_salida - registro_hoy.hora_entrada
                horas = int(duracion.total_seconds() // 3600)
                minutos = int((duracion.total_seconds() % 3600) // 60)
                duracion_str = f"{horas}h {minutos}m"
                status = "Completed"

            attendance_record = UserAttendanceRecord(
                hora_entrada=hora_entrada_str,
                hora_salida=hora_salida_str,
                duracion_jornada=duracion_str,
                status=status
            )

        user_with_attendance = UserWithAttendance(
            id=employee.id,
            name=employee.name,
            firstName=employee.firstName,
            lastName=employee.lastName,
            email=employee.email,
            role=employee.role,
            isActive=employee.isActive,
            createdAt=employee.createdAt,
            attendance_today=attendance_record
        )
        response_list.append(user_with_attendance)

    return response_list

# ============= ENDPOINTS LEGACY MEJORADOS PARA COMPATIBILIDAD =============

@app.post("/tokens/{qr_id}/record_scan", tags=["Legacy"])
async def legacy_record_scan(qr_id: str, db: Session = Depends(get_db)):
    """🔄 Endpoint legacy para compatibilidad con el scanner existente (con validación NestJS)"""
    try:
        # Convertir qr_id a int
        qr_id_int = int(qr_id)
        escaneo = await record_scan(qr_id_int, db)

        return {
            "success": True,
            "message": "Escaneo registrado",
            "is_first_scan": escaneo.es_entrada,  # True si es entrada, False si es salida
            "empleado_id": escaneo.empleado_id,
            "empleado_info": escaneo.empleado_info.dict() if escaneo.empleado_info else None,
            "fecha_escaneo": escaneo.hora_entrada if escaneo.es_entrada else escaneo.hora_salida,
            "accion": "ENTRADA" if escaneo.es_entrada else "SALIDA"
        }
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ID de QR inválido"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/tokens/{qr_id}/validate", tags=["Legacy"])
async def legacy_validate(qr_id: str, db: Session = Depends(get_db)):
    """🔄 Endpoint legacy para validación compatible con el scanner (con datos NestJS)"""
    try:
        qr_id_int = int(qr_id)
        validation = await validate_qr(qr_id_int, db)

        if validation.valid:
            # Obtener registros para mostrar en el scanner
            registros = db.query(RegistroEscaneo).filter(
                RegistroEscaneo.qr_id == qr_id_int
            ).order_by(desc(RegistroEscaneo.fecha)).all()

            # Crear lista de escaneos previos (entradas y salidas)
            previous_scans = []
            for registro in registros:
                previous_scans.append(registro.hora_entrada.isoformat())
                if registro.hora_salida:
                    previous_scans.append(registro.hora_salida.isoformat())

            # Obtener último registro para mostrar información
            ultimo_registro = registros[0] if registros else None
            usado_en = None
            if ultimo_registro:
                if ultimo_registro.hora_salida:
                    usado_en = ultimo_registro.hora_salida.isoformat()
                else:
                    usado_en = ultimo_registro.hora_entrada.isoformat()

            return {
                "valid": True,
                "message": validation.message,
                "token_data": {
                    "empleado_id": validation.qr_data["empleado_id"],
                    "empleado_info": validation.empleado_info.dict() if validation.empleado_info else None,
                    "estado": "ACTIVO",
                    "usado_en": usado_en,
                    "accion": validation.accion
                },
                "first_scan": validation.accion == "ENTRADA",
                "previous_scans": previous_scans
            }
        else:
            return {
                "valid": False,
                "message": validation.message,
                "token_data": {
                    **(validation.qr_data or {}),
                    "empleado_info": validation.empleado_info.dict() if validation.empleado_info else None
                },
                "first_scan": False,
                "previous_scans": []
            }
    except ValueError:
        return {
            "valid": False,
            "message": "ID de QR inválido",
            "token_data": {},
            "first_scan": False,
            "previous_scans": []
        }
    except Exception as e:
        return {
            "valid": False,
            "message": f"Error del servidor: {str(e)}",
            "token_data": {},
            "first_scan": False,
            "previous_scans": []
        }

# ============= ENDPOINTS ADICIONALES PARA SINCRONIZACIÓN =============

@app.post("/admin/sync-employees", tags=["Administration"])
async def sync_employees_qrs(db: Session = Depends(get_db)):
    """🔄 Sincroniza empleados del backend NestJS con códigos QR existentes"""

    # Obtener todos los empleados del backend
    all_employees = await get_all_employees()
    backend_status = await check_backend_status()

    if backend_status != "CONNECTED":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Backend NestJS no disponible: {backend_status}"
        )

    # Obtener QRs existentes activos
    existing_qrs = db.query(QRCode).filter(QRCode.activo == True).all()
    existing_employee_ids = {qr.empleado_id for qr in existing_qrs}

    # Empleados en backend pero sin QR activo
    employees_without_qr = [emp for emp in all_employees if emp.id not in existing_employee_ids]

    # QRs activos de empleados que ya no existen en backend
    backend_employee_ids = {emp.id for emp in all_employees}
    orphaned_qrs = [qr for qr in existing_qrs if qr.empleado_id not in backend_employee_ids]

    return {
        "backend_status": backend_status,
        "total_employees_in_backend": len(all_employees),
        "total_active_qrs_in_system": len(existing_qrs),
        "employees_without_qr": [
            {
                "id": emp.id,
                "name": emp.name,
                "email": emp.email,
                "role": emp.role
            }
            for emp in employees_without_qr
        ],
        "orphaned_qrs": [
            {
                "qr_id": qr.id,
                "empleado_id": qr.empleado_id,
                "activo": qr.activo,
                "creado_en": qr.creado_en.isoformat()
            }
            for qr in orphaned_qrs
        ],
        "sync_needed": len(employees_without_qr) > 0 or len(orphaned_qrs) > 0
    }

@app.post("/admin/cleanup/orphaned-qrs", tags=["Administration"])
async def cleanup_orphaned_qrs(db: Session = Depends(get_db)):
    """🧹 Limpia códigos QR de empleados que ya no existen en el backend NestJS"""

    backend_status = await check_backend_status()
    if backend_status != "CONNECTED":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Backend NestJS no disponible: {backend_status}"
        )

    # Obtener empleados del backend
    all_employees = await get_all_employees()
    backend_employee_ids = {emp.id for emp in all_employees}

    # Encontrar QRs huérfanos (solo los activos)
    orphaned_qrs = db.query(QRCode).filter(
        ~QRCode.empleado_id.in_(backend_employee_ids),
        QRCode.activo == True
    ).all()

    cleaned_qrs = []

    for qr in orphaned_qrs:
        # Contar escaneos antes de desactivar
        scans_count = db.query(RegistroEscaneo).filter(RegistroEscaneo.qr_id == qr.id).count()

        # En lugar de eliminar, desactivar el QR para mantener historial
        qr.activo = False
        
        cleaned_qrs.append({
            "qr_id": qr.id,
            "empleado_id": qr.empleado_id,
            "action": "deactivated",
            "scans_preserved": scans_count
        })

    db.commit()

    return {
        "success": True,
        "message": f"Limpieza completada: {len(cleaned_qrs)} QRs huérfanos desactivados (historial preservado)",
        "cleaned_qrs": cleaned_qrs,
        "total_qrs_deactivated": len(cleaned_qrs),
        "backend_status": backend_status,
        "note": "Los QRs fueron desactivados en lugar de eliminados para preservar el historial de escaneos"
    }

# ============= ENDPOINT DE SALUD PARA MONITOREO =============

@app.get("/health", tags=["System"])
async def health_check(db: Session = Depends(get_db)):
    """🏥 Endpoint de salud para monitoreo del sistema integrado"""

    try:
        # Verificar conexión a base de datos
        db.execute("SELECT 1")
        db_status = "OK"
    except Exception as e:
        db_status = f"ERROR: {str(e)}"

    # Verificar backend NestJS
    backend_status = await check_backend_status()

    # Estadísticas rápidas
    try:
        total_qrs = db.query(QRCode).count()
        total_qrs_activos = db.query(QRCode).filter(QRCode.activo == True).count()
        total_escaneos = db.query(RegistroEscaneo).count()
        stats_status = "OK"
    except Exception as e:
        total_qrs = 0
        total_qrs_activos = 0
        total_escaneos = 0
        stats_status = f"ERROR: {str(e)}"

    overall_status = "HEALTHY" if all([
        db_status == "OK",
        backend_status == "CONNECTED",
        stats_status == "OK"
    ]) else "DEGRADED"

    return {
        "status": overall_status,
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.3.0",
        "components": {
            "database": db_status,
            "nestjs_backend": backend_status,
            "statistics": stats_status,
            "qr_generation": "OK" if QR_AVAILABLE else "LIMITED"
        },
        "metrics": {
            "total_qrs": total_qrs,
            "total_qrs_activos": total_qrs_activos,
            "total_escaneos": total_escaneos
        },
        "backend_url": NESTJS_BACKEND_URL,
        "new_features": [
            "Regeneración automática de QR en login",
            "Preservación de historial en limpieza de QRs huérfanos",
            "Endpoint /qr/login para regenerar QR automáticamente"
        ]
    }

# ============= CONFIGURACIÓN PARA RAILWAY =============

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Iniciando servidor en puerto {port}")
    print(f"🌐 Backend NestJS: {NESTJS_BACKEND_URL}")
    print(f"📱 QR disponible: {QR_AVAILABLE}")
    print(f"🔧 CORS configurado para localhost:4200")
    print(f"🆕 Funcionalidad de regeneración de QR en login activada")
    uvicorn.run(app, host="0.0.0.0", port=port)