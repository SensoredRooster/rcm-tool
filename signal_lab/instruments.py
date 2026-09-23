"""SCPI/VISA instrument adapters with explicit measurement/source roles."""
from __future__ import annotations

from dataclasses import dataclass

try:
    import pyvisa  # type: ignore
except Exception:
    pyvisa = None


@dataclass(frozen=True)
class InstrumentCapabilities:
    frequency: bool = False
    period: bool = False
    duty_cycle: bool = False
    waveform_capture: bool = False
    phase_noise: bool = False
    generator_output: bool = False
    high_resolution_timestamps: bool = False


@dataclass
class SafetyLimits:
    max_frequency_hz: float = 20_000_000.0
    max_amplitude_vpp: float = 1.0
    max_abs_offset_v: float = 0.5
    max_duration_s: float = 600.0

    def validate(self, *, frequency_hz: float, amplitude_vpp: float, offset_v: float) -> None:
        if not (0.0 <= frequency_hz <= self.max_frequency_hz):
            raise ValueError(f"Frequency {frequency_hz:g} Hz exceeds configured safety limit")
        if not (0.0 <= amplitude_vpp <= self.max_amplitude_vpp):
            raise ValueError(f"Amplitude {amplitude_vpp:g} Vpp exceeds configured safety limit")
        if abs(offset_v) > self.max_abs_offset_v:
            raise ValueError(f"Offset {offset_v:g} V exceeds configured safety limit")


class InstrumentAdapter:
    name = "Instrument"
    capabilities = InstrumentCapabilities()

    def identify(self) -> str:
        return self.name

    def set_output(self, enabled: bool) -> None:
        if enabled:
            raise RuntimeError("This instrument adapter does not support generator output")

    def output_enabled(self) -> bool:
        return False

    def measure_frequency_hz(self) -> float | None:
        return None

    def measure_duty_cycle_percent(self) -> float | None:
        return None

    def close(self) -> None:
        pass


class SimulatedInstrument(InstrumentAdapter):
    name = "Simulation Instrument"
    capabilities = InstrumentCapabilities(
        frequency=True, period=True, duty_cycle=True, waveform_capture=True,
        generator_output=True, high_resolution_timestamps=True,
    )

    def __init__(self) -> None:
        self._output = False
        self.frequency_hz = 1000.0
        self.amplitude_vpp = 0.1
        self.offset_v = 0.0
        self.waveform = "SINE"

    def set_output(self, enabled: bool) -> None:
        self._output = bool(enabled)

    def output_enabled(self) -> bool:
        return self._output


class _VisaBase(InstrumentAdapter):
    def __init__(self, resource_name: str, timeout_ms: int = 1500) -> None:
        if pyvisa is None:
            raise RuntimeError("PyVISA is not installed")
        self.resource_name = resource_name
        self.rm = pyvisa.ResourceManager()
        self.resource = self.rm.open_resource(resource_name)
        self.resource.timeout = timeout_ms

    def identify(self) -> str:
        try:
            return str(self.resource.query("*IDN?")).strip()
        except Exception:
            return f"SCPI instrument ({self.resource_name})"

    def write(self, command: str) -> None:
        self.resource.write(command)

    def query(self, command: str) -> str:
        return str(self.resource.query(command)).strip()

    def close(self) -> None:
        try:
            self.resource.close()
        finally:
            self.rm.close()


class VisaScpiMeasurementInstrument(_VisaBase):
    """Read-only role for counters/scopes/analyzers. Construction sends no OUTP command."""

    name = "VISA/SCPI measurement instrument"
    capabilities = InstrumentCapabilities(frequency=True, period=True, duty_cycle=True)

    def _query_float(self, commands: tuple[str, ...]) -> float | None:
        for command in commands:
            try:
                value = float(self.resource.query(command))
                if value == value:
                    return value
            except Exception:
                continue
        return None

    def measure_frequency_hz(self) -> float | None:
        return self._query_float((
            "MEAS:FREQ?", "MEASure:FREQuency?",
            "FETCh:FREQuency?", "READ:FREQuency?",
        ))

    def measure_duty_cycle_percent(self) -> float | None:
        return self._query_float((
            "MEAS:DUTY?", "MEASure:DCYCle?", "FETCh:DCYCle?",
        ))


class VisaScpiGenerator(_VisaBase):
    """Generic SCPI generator role. Output is forced OFF immediately on connect."""

    name = "VISA/SCPI generator"
    capabilities = InstrumentCapabilities(generator_output=True)

    def __init__(self, resource_name: str, timeout_ms: int = 1500) -> None:
        super().__init__(resource_name, timeout_ms)
        self._output = False
        try:
            self.set_output(False)
        except Exception:
            super().close()
            raise RuntimeError("Could not establish a safe OUTPUT OFF state on this SCPI resource")

    def set_output(self, enabled: bool) -> None:
        self.resource.write("OUTP ON" if enabled else "OUTP OFF")
        self._output = bool(enabled)

    def output_enabled(self) -> bool:
        return self._output

    def configure_generator(
        self, *, frequency_hz: float, amplitude_vpp: float, offset_v: float,
        waveform: str, limits: SafetyLimits,
    ) -> None:
        limits.validate(
            frequency_hz=frequency_hz,
            amplitude_vpp=amplitude_vpp,
            offset_v=offset_v,
        )
        wave = waveform.upper()
        if wave not in {"SINE", "SQU", "RAMP", "PULS", "NOIS"}:
            raise ValueError("Unsupported waveform")
        self.resource.write(f"FUNC {wave}")
        self.resource.write(f"FREQ {frequency_hz:.12g}")
        self.resource.write(f"VOLT {amplitude_vpp:.12g}")
        self.resource.write(f"VOLT:OFFS {offset_v:.12g}")

    def close(self) -> None:
        try:
            self.set_output(False)
        except Exception:
            pass
        super().close()


VisaScpiInstrument = VisaScpiGenerator


def list_visa_resources() -> list[str]:
    if pyvisa is None:
        return []
    try:
        rm = pyvisa.ResourceManager()
        try:
            return list(rm.list_resources())
        finally:
            rm.close()
    except Exception:
        return []
