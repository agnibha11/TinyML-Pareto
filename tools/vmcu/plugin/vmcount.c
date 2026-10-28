/*
 * vmcount.c - QEMU TCG plugin: exact instruction counts and a Cortex-M4 cycle estimate between two marker PCs.
 *
 * The firmware calls vm_mark_begin() and vm_mark_end() (two distinct, never-inlined functions) around every
 * measured region. At each vm_mark_end() the plugin prints one line to the QEMU log:
 *
 *     VMC insn=<executed instructions> cyc=<estimated cycles> ld=<loads> st=<stores> br=<branches> fp=<VFP ops>
 *
 * counting everything executed after the matching vm_mark_begin(). The harness pairs these lines, in order, with
 * the labels the firmware prints over semihosting.
 *
 * Per-instruction costs come from a table written by tools/vmcu/cyclemodel.py (one line per instruction address
 * of the ELF: "pc cyc ld st br fp", hex pc), because the distribution QEMU has no disassembler for plugins.
 * Addresses missing from the table count as 1 instruction, 1 cycle.
 *
 * Build: gcc -O2 -shared -fPIC -I<dir with qemu-plugin.h v8.2.x> vmcount.c -o libvmcount.so \
 *            $(pkg-config --cflags glib-2.0)
 * Use:   qemu-system-arm ... -plugin ./libvmcount.so,begin=0x..,end=0x..,costs=costs.txt -d plugin -D vmc.log
 */
#include <glib.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

enum { C_INSN, C_CYC, C_LD, C_ST, C_BR, C_FP, C_N };
typedef struct { uint64_t w[C_N]; } cost_t;

static uint64_t g_cnt[C_N];
static uint64_t g_mark[C_N];
static uint64_t g_begin_pc, g_end_pc;
static int g_open = 0;
static GHashTable *g_costs;           /* pc -> cost_t* */
static uint64_t g_unknown = 0;
static const cost_t k_default = {{1, 1, 0, 0, 0, 0}};

static void on_begin(unsigned int vcpu, void *ud) {
    memcpy(g_mark, g_cnt, sizeof g_cnt);
    g_open = 1;
}

static void on_end(unsigned int vcpu, void *ud) {
    char buf[220];
    if (!g_open) {
        qemu_plugin_outs("VMC error=end_without_begin\n");
        return;
    }
    snprintf(buf, sizeof buf, "VMC insn=%" PRIu64 " cyc=%" PRIu64 " ld=%" PRIu64 " st=%" PRIu64 " br=%" PRIu64
             " fp=%" PRIu64 "\n", g_cnt[C_INSN] - g_mark[C_INSN], g_cnt[C_CYC] - g_mark[C_CYC],
             g_cnt[C_LD] - g_mark[C_LD], g_cnt[C_ST] - g_mark[C_ST], g_cnt[C_BR] - g_mark[C_BR],
             g_cnt[C_FP] - g_mark[C_FP]);
    qemu_plugin_outs(buf);
    g_open = 0;
}

static void on_tb_trans(qemu_plugin_id_t id, struct qemu_plugin_tb *tb) {
    size_t n = qemu_plugin_tb_n_insns(tb);
    for (size_t i = 0; i < n; ++i) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint64_t pc = qemu_plugin_insn_vaddr(insn);
        if (pc == g_begin_pc)
            qemu_plugin_register_vcpu_insn_exec_cb(insn, on_begin, QEMU_PLUGIN_CB_NO_REGS, NULL);
        if (pc == g_end_pc)
            qemu_plugin_register_vcpu_insn_exec_cb(insn, on_end, QEMU_PLUGIN_CB_NO_REGS, NULL);
        const cost_t *c = g_costs ? g_hash_table_lookup(g_costs, &pc) : NULL;
        if (!c) {
            c = &k_default;
            ++g_unknown;
        }
        for (int k = 0; k < C_N; ++k)
            if (c->w[k])
                qemu_plugin_register_vcpu_insn_exec_inline(insn, QEMU_PLUGIN_INLINE_ADD_U64, &g_cnt[k], c->w[k]);
    }
}

static void on_plugin_exit(qemu_plugin_id_t id, void *p) {
    char buf[96];
    snprintf(buf, sizeof buf, "VMC_DONE untabled_translated_insns=%" PRIu64 "\n", g_unknown);
    qemu_plugin_outs(buf);
}

static int load_costs(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    g_costs = g_hash_table_new(g_int64_hash, g_int64_equal);
    char line[128];
    while (fgets(line, sizeof line, f)) {
        uint64_t pc, w[5];
        if (sscanf(line, "%" SCNx64 " %" SCNu64 " %" SCNu64 " %" SCNu64 " %" SCNu64 " %" SCNu64, &pc, &w[0], &w[1],
                   &w[2], &w[3], &w[4]) != 6)
            continue;
        uint64_t *key = g_new(uint64_t, 1);
        cost_t *c = g_new(cost_t, 1);
        *key = pc;
        c->w[C_INSN] = 1;
        c->w[C_CYC] = w[0];
        c->w[C_LD] = w[1];
        c->w[C_ST] = w[2];
        c->w[C_BR] = w[3];
        c->w[C_FP] = w[4];
        g_hash_table_insert(g_costs, key, c);
    }
    fclose(f);
    return 0;
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id, const qemu_info_t *info, int argc, char **argv) {
    for (int i = 0; i < argc; ++i) {
        if (strncmp(argv[i], "begin=", 6) == 0) g_begin_pc = strtoull(argv[i] + 6, NULL, 0) & ~1ull;
        else if (strncmp(argv[i], "end=", 4) == 0) g_end_pc = strtoull(argv[i] + 4, NULL, 0) & ~1ull;
        else if (strncmp(argv[i], "costs=", 6) == 0 && load_costs(argv[i] + 6) != 0) {
            fprintf(stderr, "vmcount: cannot read %s\n", argv[i] + 6);
            return -1;
        }
    }
    if (!g_begin_pc || !g_end_pc) {
        fprintf(stderr, "vmcount: need begin=<addr>,end=<addr>[,costs=<file>]\n");
        return -1;
    }
    qemu_plugin_register_vcpu_tb_trans_cb(id, on_tb_trans);
    qemu_plugin_register_atexit_cb(id, on_plugin_exit, NULL);
    return 0;
}
