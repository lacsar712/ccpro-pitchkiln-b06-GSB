from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods, require_POST

from .forms import OpenCookRunForm, PhaseChangeForm, ResinLotForm, SoftPointProbeForm
from .models import CookRun, FireHearth, ResinLot
from .services.floor_rules import DRAWING_SOFT_POINT_MAX, change_hearth_phase

PHASE_KEYS = {key for key, _label in FireHearth.PHASE_CHOICES}


def _wants_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _hearths_for_board():
    """看板唯一数据源：无筛全量灶台 + 预取进行中值守。"""
    return FireHearth.objects.prefetch_related(
        Prefetch(
            "runs",
            queryset=CookRun.objects.filter(closedAt__isnull=True)
            .select_related("resinLot")
            .prefetch_related("probes"),
            to_attr="open_runs_cache",
        )
    ).order_by("lane", "tag")


def _board_context(active_phase=""):
    """
    整页与 HTMX 局部网格共用的上下文。

    瓦片与对账同源自一份「无筛全量」列表：灶台总数 / 各相位图例数 /
    未收灶值守数一律按无筛全量复算；相位筛只在内存里过滤展示子集，
    不另起第二趟查询，杜绝两处各算出现差 1。
    """
    if active_phase not in PHASE_KEYS:
        active_phase = ""
    hearths_all = list(_hearths_for_board())
    if active_phase:
        tiles = [h for h in hearths_all if h.phase == active_phase]
    else:
        tiles = hearths_all
    lanes = {}
    for h in tiles:
        lanes.setdefault(h.lane, []).append(h)
    phase_legend = [
        (key, label, sum(1 for h in hearths_all if h.phase == key))
        for key, label in FireHearth.PHASE_CHOICES
    ]
    recon = {
        "hearth_total": len(hearths_all),
        "open_run_total": sum(1 for h in hearths_all if h.open_runs_cache),
    }
    return {
        "lanes": sorted(lanes.items()),
        "phase_legend": phase_legend,
        "phase_choices": FireHearth.PHASE_CHOICES,
        "active_phase": active_phase,
        "recon": recon,
    }


def _drawer_context(hearth):
    open_run = hearth.open_run()
    probes = []
    if open_run:
        probes = list(open_run.probes.order_by("-sampledAt", "-id"))
    return {
        "hearth": hearth,
        "open_run": open_run,
        "probes": probes,
        "phase_form": PhaseChangeForm(hearth=hearth),
        "probe_form": SoftPointProbeForm() if open_run else None,
        "open_run_form": OpenCookRunForm(hearth=hearth) if open_run is None else None,
        "drawing_soft_point_max": DRAWING_SOFT_POINT_MAX,
    }


@login_required
def home(request):
    ctx = _board_context(request.GET.get("phase", ""))
    drawer_pk = request.GET.get("hearth")
    if drawer_pk:
        try:
            hearth = FireHearth.objects.get(pk=drawer_pk)
            ctx.update(_drawer_context(hearth))
            ctx["drawer_open"] = True
        except (FireHearth.DoesNotExist, ValueError):
            ctx["drawer_open"] = False
    else:
        ctx["drawer_open"] = False
    return render(request, "floor/board.html", ctx)


@login_required
def floor_grid_partial(request):
    ctx = _board_context(request.GET.get("phase", ""))
    html = render_to_string("floor/_grid.html", ctx, request=request)
    return HttpResponse(html)


@login_required
def hearth_drawer(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    ctx = _drawer_context(hearth)
    if _wants_htmx(request):
        return render(request, "floor/_drawer.html", ctx)
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def change_phase(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = PhaseChangeForm(request.POST, hearth=hearth)
    if form.is_valid():
        try:
            change_hearth_phase(hearth, form.cleaned_data["phase"])
            messages.success(request, f"灶牌 {hearth.tag} 相位已更新")
        except ValidationError as exc:
            msg = (
                exc.message_dict.get("phase") if hasattr(exc, "message_dict") else None
            )
            messages.error(request, msg[0] if msg else str(exc))
    else:
        err = form.errors.get("phase")
        messages.error(request, err[0] if err else "相位切换失败")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def add_probe(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守，无法登记探针")
        return redirect(f"/?hearth={pk}")

    form = SoftPointProbeForm(request.POST)
    if form.is_valid():
        probe = form.save(commit=False)
        probe.run = open_run
        probe.save()
        messages.success(request, f"已登记探针 {probe.softPointC}℃")
    else:
        messages.error(request, "探针登记失败，请检查输入")

    if _wants_htmx(request):
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def open_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = OpenCookRunForm(request.POST, hearth=hearth)
    if form.is_valid():
        run = form.save(commit=False)
        run.hearth = hearth
        run.save()
        if hearth.phase == FireHearth.PHASE_COLD:
            hearth.phase = FireHearth.PHASE_CHARGING
            hearth.save(update_fields=["phase"])
        messages.success(request, "新值守已开灶")
    else:
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
            break

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def close_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守可收灶")
    else:
        open_run.closedAt = timezone.now()
        open_run.save(update_fields=["closedAt"])
        hearth.phase = FireHearth.PHASE_COLD
        hearth.save(update_fields=["phase"])
        messages.success(request, "值守已收灶，灶台回冷灶")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_http_methods(["GET", "POST"])
def resin_lot_feed(request):
    if request.method == "POST":
        form = ResinLotForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "来脂批已登记")
            url = reverse("resin_lot_feed")
            origin = request.GET.get("origin", "")
            if origin:
                url = f"{url}?{urlencode({'origin': origin})}"
            return redirect(url)
    else:
        form = ResinLotForm(
            initial={
                "receivedAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            }
        )

    # 无筛全量：卡片与对账共用同一列表，不切片、不另起聚合查询
    lots_all = list(ResinLot.objects.all())
    origins = sorted({lot.originPlace for lot in lots_all})
    active_origin = request.GET.get("origin", "")
    if active_origin not in origins:
        active_origin = ""
    if active_origin:
        lots = [lot for lot in lots_all if lot.originPlace == active_origin]
    else:
        lots = lots_all
    return render(
        request,
        "resin/feed.html",
        {
            "lots": lots,
            "form": form,
            "origins": origins,
            "active_origin": active_origin,
            "recon": {"lot_total": len(lots_all)},
        },
    )
