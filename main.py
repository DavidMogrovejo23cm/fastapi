from typing import Union, Optional, List
from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
import secrets
import string
from datetime import datetime
from pydantic import BaseModel
from database import get_db, create_tables, QRCode, RegistroEscaneo
from sqlalchemy import desc
import httpx
import asyncio

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

app = FastAPI(
    title="QR Attendance API - Integrado con NestJS",
    description="""
    ## 🎯 API para Control de Asistencia con Códigos QR - Integrada con Backend de Usuarios

    Sistema integrado que consume el backend de NestJS para validar empleados antes de generar códigos QR.

    ### 🔧 Características principales:
    - **Validación de empleados** desde backend NestJS
    - **Generación de QRs únicos** por empleado válido
    - **Registro automático** de entrada y salida
    - **Un registro por día** por empleado
    - **Reportes diarios** completos con información de empleados
    - **Integración completa** con sistema de usuarios existente

    ### 📊 Flujo de trabajo integrado:
    1. **Validar empleado** en backend NestJS
    2. **Generar QR** para empleado válido
    3. **Primer escaneo** del día → ENTRADA
    4. **Segundo escaneo** del día → SALIDA
    5. **Consultar reportes** con datos de empleados

    ### 🌐 Backend integrado:
    - **NestJS Backend**: `https://backtofastapi-production.up.railway.app`
    - **Endpoints consumidos**: `/user/{id}`, `/user`
    """,
    version="2.0.0",
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

# Reiniciar la base de datos al iniciar (elimina esquema anterior)
print("🚀 Iniciando aplicación integrada...")

# ============= MODELOS PYDANTIC =============

class EmployeeInfo(BaseModel):
    id: int
    name: str
    email: str
    role: str

class QRGenerationRequest(BaseModel):
    empleado_id: int

class QRCodeResponse(BaseModel):
    id: int
    empleado_id: int
    empleado_info: Optional[EmployeeInfo] = None
    qr_code_base64: str
    creado_en: str
    activo: bool
    total_escaneos: int

class EscaneoResponse(BaseModel):
    id: int
    qr_id: int
    empleado_id: int
    empleado_info: Optional[EmployeeInfo] = None
    fecha: str
    hora_entrada: str
    hora_salida: Optional[str] = None
    es_entrada: bool  # True si es entrada, False si es salida
    duracion_jornada: Optional[str] = None  # Duración en formato "8h 30m" si hay salida

class ValidationResponse(BaseModel):
    valid: bool
    message: str
    qr_data: Optional[dict] = None
    empleado_info: Optional[EmployeeInfo] = None
    accion: Optional[str] = None  # "ENTRADA" o "SALIDA"

class AttendanceStatsResponse(BaseModel):
    total_qrs: int
    total_escaneos: int
    empleados_registrados: int
    escaneos_hoy: int
    backend_status: str

# ============= FUNCIONES PARA CONSUMIR BACKEND NESTJS =============

async def get_employee_by_id(empleado_id: int) -> Optional[EmployeeInfo]:
    """Obtiene información del empleado desde el backend NestJS"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{NESTJS_BACKEND_URL}/user/{empleado_id}",
                timeout=10.0
            )
            
            if response.status_code == 200:
                user_data = response.json()
                return EmployeeInfo(
                    id=user_data["id"],
                    name=user_data["name"],
                    email=user_data["email"],
                    role=user_data["role"]
                )
            elif response.status_code == 404:
                return None
            else:
                print(f"❌ Error obteniendo empleado {empleado_id}: {response.status_code}")
                return None
                
    except httpx.TimeoutException:
        print(f"⏰ Timeout obteniendo empleado {empleado_id}")
        return None
    except Exception as e:
        print(f"❌ Error de conexión obteniendo empleado {empleado_id}: {e}")
        return None

async def get_all_employees() -> List[EmployeeInfo]:
    """Obtiene todos los empleados desde el backend NestJS"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{NESTJS_BACKEND_URL}/user",
                timeout=15.0
            )
            
            if response.status_code == 200:
                users_data = response.json()
                return [
                    EmployeeInfo(
                        id=user["id"],
                        name=user["name"],
                        email=user["email"],
                        role=user["role"]
                    )
                    for user in users_data
                ]
            else:
                print(f"❌ Error obteniendo todos los empleados: {response.status_code}")
                return []
                
    except httpx.TimeoutException:
        print("⏰ Timeout obteniendo todos los empleados")
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

async def qr_to_response(qr_code: QRCode, db: Session) -> QRCodeResponse:
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
        total_escaneos=total_escaneos
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

# ============= ENDPOINTS =============

@app.get("/", tags=["System"])
async def read_root():
    backend_status = await check_backend_status()
    return {
        "Hello": "QR Attendance API - Integrado con NestJS",
        "version": "2.0.0",
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
            "Control de asistencia con validación de usuarios"
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
    """🔍 Obtiene el QR código de un empleado específico si existe"""
    # Verificar que el empleado existe
    employee = await get_employee_by_id(empleado_id)
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Empleado con ID {empleado_id} no encontrado en el sistema"
        )
    
    # Buscar QR existente
    existing_qr = db.query(QRCode).filter(
        QRCode.empleado_id == empleado_id,
        QRCode.activo == True
    ).first()
    
    if existing_qr:
        return await qr_to_response(existing_qr, db)
    else:
        return None

# ============= ENDPOINTS DE QR CODES INTEGRADOS =============

@app.post("/qr/generate", response_model=QRCodeResponse, tags=["QR Codes"])
async def generate_qr(request: QRGenerationRequest, db: Session = Depends(get_db)):
    """
    ## 🎯 Genera un nuevo código QR para un empleado (con validación en NestJS)

    **Comportamiento:**
    - Valida que el empleado existe en el backend NestJS
    - Si el empleado ya tiene un QR activo, devuelve el existente
    - Si no tiene QR, crea uno nuevo
    - El QR es único y reutilizable diariamente

    **Validaciones:**
    - Empleado debe existir en el sistema NestJS
    - Solo empleados válidos pueden tener QR

    **Parámetros:**
    - `empleado_id`: ID único del empleado (validado contra NestJS)

    **Respuesta:**
    - Información completa del QR generado
    - Datos del empleado desde NestJS
    - Código QR en formato base64
    - Total de escaneos realizados
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
        print(f"📋 QR existente encontrado para empleado {request.empleado_id}")
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
    return await qr_to_response(db_qr, db)

@app.get("/qr/{qr_id}/validate", response_model=ValidationResponse, tags=["QR Codes"])
async def validate_qr(qr_id: int, db: Session = Depends(get_db)):
    """
    ## ✅ Valida un código QR y determina la próxima acción (con info del empleado)

    **Comportamiento:**
    - Verifica si el QR existe y está activo
    - Valida que el empleado aún existe en el backend NestJS
    - Determina si el próximo escaneo será ENTRADA o SALIDA
    - Informa si ya completó entrada y salida del día

    **Respuestas posibles:**
    - `ENTRADA`: Primer escaneo del día
    - `SALIDA`: Ya tiene entrada, registrará salida
    - `COMPLETADO`: Ya registró entrada y salida hoy
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
            message="Código QR desactivado",
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

    **Lógica del sistema:**
    - **Validación**: Verifica que el empleado existe en NestJS
    - **Primer escaneo del día**: Registra ENTRADA con hora actual
    - **Segundo escaneo del día**: Actualiza el registro con SALIDA
    - **Tercer escaneo**: Error - ya completó entrada y salida

    **Cálculos automáticos:**
    - Duración de jornada cuando hay salida
    - Fecha del registro
    - Validación de QR activo y empleado válido
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
            detail="Código QR desactivado"
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

    if registro_hoy:
        if registro_hoy.hora_salida is None:
            # Registrar salida
            print(f"🚪 Registrando SALIDA para {employee.name}")
            registro_hoy.hora_salida = ahora
            db.commit()
            db.refresh(registro_hoy)
            return await escaneo_to_response(registro_hoy, db)
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

        return await escaneo_to_response(nuevo_registro, db)

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

    **Formato de fecha:** YYYY-MM-DD (ejemplo: 2025-07-29)

    **Incluye:**
    - Estadísticas generales del día
    - Detalle por empleado con información completa desde NestJS
    - Duración de jornadas completadas
    - Empleados sin salida registrada
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
        "version": "2.0.0",
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
            "Reportes enriquecidos con información completa"
        ]
    }

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
    
    # Obtener QRs existentes
    existing_qrs = db.query(QRCode).all()
    existing_employee_ids = {qr.empleado_id for qr in existing_qrs}
    
    # Empleados en backend pero sin QR
    employees_without_qr = [emp for emp in all_employees if emp.id not in existing_employee_ids]
    
    # QRs de empleados que ya no existen en backend
    backend_employee_ids = {emp.id for emp in all_employees}
    orphaned_qrs = [qr for qr in existing_qrs if qr.empleado_id not in backend_employee_ids]
    
    return {
        "backend_status": backend_status,
        "total_employees_in_backend": len(all_employees),
        "total_qrs_in_system": len(existing_qrs),
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
    
    # Encontrar QRs huérfanos
    orphaned_qrs = db.query(QRCode).filter(
        ~QRCode.empleado_id.in_(backend_employee_ids)
    ).all()
    
    cleaned_qrs = []
    total_scans_deleted = 0
    
    for qr in orphaned_qrs:
        # Contar escaneos antes de eliminar
        scans_count = db.query(RegistroEscaneo).filter(RegistroEscaneo.qr_id == qr.id).count()
        
        # Eliminar escaneos asociados
        db.query(RegistroEscaneo).filter(RegistroEscaneo.qr_id == qr.id).delete()
        
        # Eliminar QR
        db.delete(qr)
        
        cleaned_qrs.append({
            "qr_id": qr.id,
            "empleado_id": qr.empleado_id,
            "scans_deleted": scans_count
        })
        
        total_scans_deleted += scans_count
    
    db.commit()
    
    return {
        "success": True,
        "message": f"Limpieza completada: {len(cleaned_qrs)} QRs huérfanos eliminados",
        "cleaned_qrs": cleaned_qrs,
        "total_qrs_deleted": len(cleaned_qrs),
        "total_scans_deleted": total_scans_deleted,
        "backend_status": backend_status
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
        total_escaneos = db.query(RegistroEscaneo).count()
        stats_status = "OK"
    except Exception as e:
        total_qrs = 0
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
        "version": "2.0.0",
        "components": {
            "database": db_status,
            "nestjs_backend": backend_status,
            "statistics": stats_status,
            "qr_generation": "OK" if QR_AVAILABLE else "LIMITED"
        },
        "metrics": {
            "total_qrs": total_qrs,
            "total_escaneos": total_escaneos
        },
        "backend_url": NESTJS_BACKEND_URL
    }

# ============= CONFIGURACIÓN PARA RAILWAY =============

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Iniciando servidor en puerto {port}")
    print(f"🌐 Backend NestJS: {NESTJS_BACKEND_URL}")
    print(f"📱 QR disponible: {QR_AVAILABLE}")
    uvicorn.run(app, host="0.0.0.0", port=port)