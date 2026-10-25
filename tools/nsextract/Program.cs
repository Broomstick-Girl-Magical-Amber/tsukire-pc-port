// nsextract - extracts the exefs and romfs of the base program of an NSP/XCI straight from the container
// (LibHac reads the program NCA in place: no intermediate copy of the partition or the NCA on disk).
//
//   nsextract info    --keys <prod.keys> --input <file.nsp|file.xci>
//       prints KEY=VALUE lines: PROGRAMID (16 hex digits), CONTENTTYPE, APPLICATIONS (count)
//   nsextract extract --keys <prod.keys> --input <file> --program-id <16 hex> --exefs <dir> --romfs <dir>
//
// Exit code 0 = ok; 1 = error (message on stderr, "ERROR: ..."). The keys are only read, never printed.
// Container detection by header, as in recomp_tsukihime.ps1: PFS0 at 0 = NSP, HEAD at 0x100 = XCI.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using LibHac;
using LibHac.Common;
using LibHac.Common.Keys;
using LibHac.Fs;
using LibHac.Fs.Fsa;
using LibHac.FsSystem;
using LibHac.Tools.Fs;
using LibHac.Tools.FsSystem;
using LibHac.Tools.FsSystem.NcaUtils;

namespace NsExtract
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            try
            {
                if (args.Length == 0) return Usage();
                string mode = args[0];
                var opt = ParseOptions(args.Skip(1).ToArray());

                string keys = Need(opt, "keys");
                string input = Need(opt, "input");
                if (!File.Exists(keys)) throw new Exception("keys file does not exist");
                if (!File.Exists(input)) throw new Exception("input file does not exist: " + input);

                var keySet = new KeySet();
                ExternalKeyReader.ReadKeyFile(keySet, prodKeysFilename: keys);

                using (var storage = new LocalStorage(input, FileAccess.Read))
                {
                    IFileSystem container = OpenContainer(storage, keySet, input);
                    SwitchFs switchFs = SwitchFs.OpenNcaDirectory(keySet, container);

                    // Application.Main is a Title; its program NCA is Main.MainNca.Nca
                    var apps = switchFs.Applications.Values.Where(a => a.Main != null && a.Main.MainNca != null).ToList();

                    if (mode == "info")
                    {
                        if (apps.Count != 1)
                            throw new Exception(apps.Count + " applications with a base program found (1 expected)");
                        Nca main = apps[0].Main.MainNca.Nca;
                        Console.WriteLine("PROGRAMID=" + main.Header.TitleId.ToString("X16"));
                        Console.WriteLine("CONTENTTYPE=" + main.Header.ContentType);
                        Console.WriteLine("APPLICATIONS=" + apps.Count);
                        return 0;
                    }

                    if (mode == "extract")
                    {
                        string wanted = Need(opt, "program-id");
                        string exefsDir = Need(opt, "exefs");
                        string romfsDir = Need(opt, "romfs");
                        ulong id = Convert.ToUInt64(wanted, 16);

                        var app = apps.FirstOrDefault(a => a.Main.MainNca.Nca.Header.TitleId == id);
                        if (app == null)
                            throw new Exception("no application with program ID " + id.ToString("X16") +
                                                " in the input (it has: " +
                                                string.Join(", ", apps.Select(a => a.Main.MainNca.Nca.Header.TitleId.ToString("X16"))) + ")");
                        Nca main = app.Main.MainNca.Nca;

                        Directory.CreateDirectory(exefsDir);
                        Directory.CreateDirectory(romfsDir);

                        Console.WriteLine("extracting exefs");
                        IFileSystem exefs = main.OpenFileSystem(NcaSectionType.Code, IntegrityCheckLevel.None);
                        exefs.Extract(exefsDir);

                        Console.WriteLine("extracting romfs");
                        IFileSystem romfs = main.OpenFileSystem(NcaSectionType.Data, IntegrityCheckLevel.None);
                        romfs.Extract(romfsDir);

                        Console.WriteLine("done");
                        return 0;
                    }
                }

                return Usage();
            }
            catch (Exception e)
            {
                Console.Error.WriteLine("ERROR: " + e.Message);
                return 1;
            }
        }

        // NSP = 'PFS0' at 0; XCI = 'HEAD' at 0x100 (the extension does not matter).
        private static IFileSystem OpenContainer(IStorage storage, KeySet keySet, string path)
        {
            byte[] head = new byte[0x104];
            using (var fs = File.OpenRead(path))
            {
                int n = fs.Read(head, 0, head.Length);
                if (n >= 4 && Encoding.ASCII.GetString(head, 0, 4) == "PFS0")
                {
                    var pfs = new PartitionFileSystem();
                    pfs.Initialize(storage).ThrowIfFailure();
                    return pfs;
                }
                if (n >= 0x104 && Encoding.ASCII.GetString(head, 0x100, 4) == "HEAD")
                {
                    var xci = new Xci(keySet, storage);
                    if (!xci.HasPartition(XciPartitionType.Secure)) throw new Exception("the XCI has no secure partition");
                    return xci.OpenPartition(XciPartitionType.Secure);
                }
            }
            throw new Exception("the input is neither an NSP (PFS0) nor an XCI (HEAD)");
        }

        private static Dictionary<string, string> ParseOptions(string[] a)
        {
            var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < a.Length; i++)
            {
                if (!a[i].StartsWith("--")) throw new Exception("unexpected argument: " + a[i]);
                if (i + 1 >= a.Length) throw new Exception("missing value for " + a[i]);
                d[a[i].Substring(2)] = a[++i];
            }
            return d;
        }

        private static string Need(Dictionary<string, string> o, string k)
        {
            if (!o.TryGetValue(k, out var v) || string.IsNullOrEmpty(v)) throw new Exception("missing --" + k);
            return v;
        }

        private static int Usage()
        {
            Console.Error.WriteLine("usage: nsextract info    --keys <prod.keys> --input <nsp|xci>");
            Console.Error.WriteLine("       nsextract extract --keys <prod.keys> --input <nsp|xci> --program-id <hex16> --exefs <dir> --romfs <dir>");
            return 1;
        }
    }
}
