from typing import Union, Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import secrets
import string
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from database import get_db, create_tables, QRCode, RegistroEscaneo
from sqlalchemy import desc, and_, or_, extract, func
import httpx
import asyncio
import traceback
from enum import Enum

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
    
    ### Nuevas Funcionalidades:
    - 🔍 Búsqueda avanzada de empleados con filtros múltiples
    - 📊 Reportes detallados por empleado con estadísticas
    - 📅 Estadísticas semanales y mensuales de asistencia
    - ⏰ Filtros por períodos de tiempo personalizables
    - 📈 Dashboard con métricas en tiempo real
    """,
    version="3.0.0",
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
            "name": "Attendance",
            "description": "🆕 Búsqueda avanzada y filtros de asistencia",
        },
        {
            "name": "Reports",
            "description": "📊 Reportes y estadísticas de asistencia con datos de empleados",
        },
        {
            "name": "Administration",
            "description": "Endpoints administrativos para gestión",
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

# ============= NUEVOS ENUMS Y MODELOS PARA FILTROS =============

class TimePeriod(str, Enum):
    today = "today"
    yesterday = "yesterday"
    this_week = "this_week"
    last_week = "last_week"
    this_month = "this_month"
    last_month = "last_month"
    custom = "custom"

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

# ============= NUEVOS MODELOS PARA FILTROS Y REPORTES =============

class AttendanceFilter(BaseModel):
    search: Optional[str] = Field(None, description="Buscar por nombre o email del empleado")
    period: Optional[TimePeriod] = Field(TimePeriod.today, description="Período de tiempo")
    start_date: Optional[str] = Field(None, description="Fecha de inicio (YYYY-MM-DD) para período personalizado")
    end_date: Optional[str] = Field(None, description="Fecha de fin (YYYY-MM-DD) para período personalizado")
    status: Optional[str] = Field(None, description="Filtrar por estado: Present, Absent, Completed")
    role: Optional[str] = Field(None, description="Filtrar por rol del empleado")
    limit: Optional[int] = Field(50, description="Número máximo de resultados", ge=1, le=200)
    offset: Optional[int] = Field(0, description="Número de registros a omitir", ge=0)

class AttendanceReport(BaseModel):
    empleado_id: int
    empleado_info: EmployeeInfo
    total_dias: int
    dias_presente: int
    dias_ausente: int
    horas_totales: str
    promedio_horas_diarias: str
    registros: List[EscaneoResponse]

class WeeklyStats(BaseModel):
    week_start: str
    week_end: str
    total_empleados: int
    empleados_activos: int
    promedio_asistencia: float
    total_horas_trabajadas: str

class MonthlyStats(BaseModel):
    month: str
    year: int
    total_empleados: int
    empleados_activos: int
    dias_laborales: int
    promedio_asistencia: float
    total_horas_trabajadas: str

# ============= FUNCIONES AUXILIARES PARA FILTROS DE FECHA =============

def get_date_range(period: TimePeriod, start_date: str = None, end_date: str = None):
    """Obtiene el rango de fechas basado en el período seleccionado"""
    today = datetime.utcnow().date()
    
    if period == TimePeriod.today:
        return today, today
    
    elif period == TimePeriod.yesterday:
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    
    elif period == TimePeriod.this_week:
        # Lunes de esta semana
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday)
        return week_start, today
    
    elif period == TimePeriod.last_week:
        # Lunes de la semana pasada
        days_since_monday = today.weekday()
        this_week_start = today - timedelta(days=days_since_monday)
        last_week_start = this_week_start - timedelta(days=7)
        last_week_end = this_week_start - timedelta(days=1)
        return last_week_start, last_week_end
    
    elif period == TimePeriod.this_month:
        # Primer día del mes actual
        month_start = today.replace(day=1)
        return month_start, today
    
    elif period == TimePeriod.last_month:
        # Primer día del mes pasado hasta último día del mes pasado
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - timedelta(days=1)
        first_day_last_month = last_day_last_month.replace(day=1)
        return first_day_last_month, last_day_last_month
    
    elif period == TimePeriod.custom:
        if start_date and end_date:
            try:
                start = datetime.fromisoformat(start_date).date()
                end = datetime.fromisoformat(end_date).date()
                return start, end
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Formato de fecha inválido. Use YYYY-MM-DD"
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Para período personalizado debe especificar start_date y end_date"
            )
    
    return today, today

def calculate_worked_hours(registros: List[RegistroEscaneo]) -> tuple:
    """Calcula las horas totales trabajadas y el promedio diario"""
    total_seconds = 0
    dias_con_horas = 0
    
    for registro in registros:
        if registro.hora_salida:
            duracion = registro.hora_salida - registro.hora_entrada
            total_seconds += duracion.total_seconds()
            dias_con_horas += 1
    
    # Convertir segundos a horas y minutos
    total_hours = int(total_seconds // 3600)
    total_minutes = int((total_seconds % 3600) // 60)
    total_horas_str = f"{total_hours}h {total_minutes}m"
    
    # Calcular promedio diario
    if dias_con_horas > 0:
        avg_seconds = total_seconds / dias_con_horas
        avg_hours = int(avg_seconds // 3600)
        avg_minutes = int((avg_seconds % 3600) // 60)
        promedio_str = f"{avg_hours}h {avg_minutes}m"
    else:
        promedio_str = "0h 0m"
    
    return total_horas_str, promedio_str

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

# ============= ENDPOINTS PRINCIPALES =============

@app.get("/", tags=["System"])
async def read_root():
    backend_status = await check_backend_status()
    return {
        "Hello": "QR Attendance API - Integrado con NestJS",
        "version": "3.0.0",
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
            "Regeneración automática de QR en cada login",
            "🆕 Búsqueda avanzada con filtros múltiples",
            "🆕 Reportes detallados por empleado",
            "🆕 Estadísticas semanales y mensuales",
            "🆕 Dashboard con métricas en tiempo real"
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
            # Validar que ha pasado al menos 1 minuto desde la entrada
            tiempo_desde_entrada = ahora - registro_hoy.hora_entrada
            if tiempo_desde_entrada.total_seconds() < 60:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Debe esperar al menos 1 minuto después de la entrada para poder registrar la salida."
                )

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

# ============= CONFIGURACIÓN PARA RAILWAY =============

if __name__ == "__main__":
    import uvicorn
    import os
    
    # Configuración del puerto para Railway
    port = int(os.environ.get("PORT", 8000))
    
    # Banner de inicio
    print("=" * 80)
    print("🚀 INICIANDO QR ATTENDANCE API v3.0.0 - INTEGRADO CON NESTJS")
    print("=" * 80)
    print(f"🌐 Servidor iniciando en puerto: {port}")
    print(f"🔗 Backend NestJS: {NESTJS_BACKEND_URL}")
    print(f"📱 Generación QR disponible: {'✅ SÍ' if QR_AVAILABLE else '❌ NO (usando placeholders)'}")
    print(f"🔧 CORS configurado para: localhost:4200, production domains")
    print(f"🗄️ Base de datos: PostgreSQL (Neon)")
    print()
    
    # Funcionalidades principales
    print("📋 FUNCIONALIDADES PRINCIPALES:")
    print("   ✅ Generación/regeneración automática de códigos QR")
    print("   ✅ Integración completa con backend NestJS")
    print("   ✅ Registro de entrada/salida con validación")
    print("   ✅ Sistema de notificaciones en tiempo real")
    print("   ✅ Control de tiempo mínimo entre entrada/salida")
    print()
    
    print("=" * 80)
    print("🎊 SISTEMA LISTO - QR ATTENDANCE API v3.0.0 INICIADO EXITOSAMENTE")
    print("=" * 80)
    print()
    
    # Iniciar servidor
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        log_level="info",
        access_log=True
    )