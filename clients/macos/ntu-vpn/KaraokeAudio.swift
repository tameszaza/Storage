import Cocoa
import CoreAudio
import AVFoundation
import Synchronization
import AudioToolbox

struct KaraokeFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private func audioAddress(_ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector:selector,mScope:scope,mElement:kAudioObjectPropertyElementMain)
}
private func audioCheck(_ status: OSStatus, _ operation: String) throws {
    if status != noErr { throw KaraokeFailure(message:"\(operation) failed (\(status)). Check Audio Recording permission for NTU VPN.") }
}
private func audioIDs(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws -> [AudioObjectID] {
    var address = audioAddress(selector,scope), bytes: UInt32 = 0
    try audioCheck(AudioObjectGetPropertyDataSize(object,&address,0,nil,&bytes),"Reading audio devices")
    if bytes == 0 { return [] }
    var values = [AudioObjectID](repeating:0,count:Int(bytes)/MemoryLayout<AudioObjectID>.size)
    try values.withUnsafeMutableBytes { raw in try audioCheck(AudioObjectGetPropertyData(object,&address,0,nil,&bytes,raw.baseAddress!),"Reading audio devices") }
    return values
}
private func audioUInt(_ object:AudioObjectID,_ selector:AudioObjectPropertySelector) throws -> UInt32 {
    var address=audioAddress(selector), result:UInt32=0, bytes=UInt32(MemoryLayout<UInt32>.size)
    try audioCheck(AudioObjectGetPropertyData(object,&address,0,nil,&bytes,&result),"Reading audio state")
    return result
}
private func audioString(_ object:AudioObjectID,_ selector:AudioObjectPropertySelector) throws -> String {
    var address=audioAddress(selector), result:Unmanaged<CFString>?, bytes=UInt32(MemoryLayout<CFString?>.size)
    try audioCheck(AudioObjectGetPropertyData(object,&address,0,nil,&bytes,&result),"Reading audio identity")
    return result?.takeRetainedValue() as String? ?? ""
}

// Safari's audio runs inside its WebKit GPU service. Core Audio identifies all
// WebKit GPU services alike, including those belonging to other apps. Resolve
// only services whose open files identify Safari's own sandbox/cache. Fail closed
// on unknown services. This runs on activation, never in the render callback.
func safariAudioProcesses() throws -> [AudioObjectID] {
    let objects = try audioIDs(AudioObjectID(kAudioObjectSystemObject),kAudioHardwarePropertyProcessObjectList)
    var result = [AudioObjectID]()
    for object in objects {
        guard let bundle = try? audioString(object,kAudioProcessPropertyBundleID) else { continue }
        if bundle == "com.apple.Safari" { result.append(object); continue }
        guard bundle == "com.apple.WebKit.GPU" else { continue }
        let pid = try audioUInt(object,kAudioProcessPropertyPID)
        let process=Process(), pipe=Pipe()
        process.executableURL=URL(fileURLWithPath:"/usr/sbin/lsof")
        process.arguments=["-n","-P","-p",String(pid),"-Fn"]
        process.standardOutput=pipe; process.standardError=FileHandle.nullDevice
        try process.run()
        let deadline=DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline:.now()+3,execute:deadline)
        let data=pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit(); deadline.cancel()
        guard process.terminationStatus == 0, let files=String(data:data,encoding:.utf8) else { continue }
        if files.split(separator:"\n").contains(where: {
            $0.hasPrefix("n/") && ($0.contains("/com.apple.WebKit.GPU+com.apple.Safari/") || $0.contains("/Library/Containers/com.apple.Safari/"))
        }) { result.append(object) }
    }
    return result.sorted()
}

// A single-clock, preallocated render path: tap input and physical output are
// delivered by one aggregate IOProc. The small FIFO only adapts Apple's pitch
// unit's render quantum; it does not bridge separate hardware clocks.
final class KaraokeDSP {
    static let capacity=65536, maximumFrames=8192
    let engine=AVAudioEngine(), pitchUnit=AVAudioUnitTimePitch()
    let samples=UnsafeMutablePointer<Float>.allocate(capacity:capacity*2)
    let output:AVAudioPCMBuffer
    var source:AVAudioSourceNode!
    var renderBlock:AVAudioEngineManualRenderingBlock!
    var readIndex=0, available=0
    var semitones:Int=0
    var underrunFrames=0, overflowFrames=0
    init(sampleRate:Double) throws {
        guard sampleRate.isFinite, sampleRate >= 8000, sampleRate <= 192000,
              let format=AVAudioFormat(standardFormatWithSampleRate:sampleRate,channels:2),
              let output=AVAudioPCMBuffer(pcmFormat:format,frameCapacity:AVAudioFrameCount(Self.maximumFrames)) else { throw KaraokeFailure(message:"Unsupported output sample rate.") }
        self.output=output
        samples.initialize(repeating:0,count:Self.capacity*2)
        source=AVAudioSourceNode(format:format) { [unowned self] silence,_,count,data in
            let buffers=UnsafeMutableAudioBufferListPointer(data)
            let requested=Int(count), supplied=min(requested,self.available)
            for channel in 0..<buffers.count {
                guard let pointer=buffers[channel].mData?.assumingMemoryBound(to:Float.self) else { continue }
                for i in 0..<supplied { pointer[i]=self.samples[((self.readIndex+i)%Self.capacity)*2+min(channel,1)] }
                for i in supplied..<requested { pointer[i]=0 }
            }
            self.readIndex=(self.readIndex+supplied)%Self.capacity; self.available-=supplied
            self.underrunFrames += requested-supplied
            silence.pointee=ObjCBool(supplied==0)
            return noErr
        }
        pitchUnit.rate=1; pitchUnit.pitch=0; pitchUnit.overlap=8
        engine.attach(source); engine.attach(pitchUnit)
        engine.connect(source,to:pitchUnit,format:format)
        engine.connect(pitchUnit,to:engine.mainMixerNode,format:format)
        try engine.enableManualRenderingMode(.realtime,format:format,maximumFrameCount:AVAudioFrameCount(Self.maximumFrames))
        engine.prepare(); try engine.start(); renderBlock=engine.manualRenderingBlock
    }
    deinit { engine.stop(); samples.deallocate() }
    func setPitch(_ value:Int) { semitones=max(-12,min(12,value)); pitchUnit.pitch=Float(semitones*100) }
    func push(_ left:UnsafePointer<Float>,_ right:UnsafePointer<Float>,frames:Int,stride:Int) {
        let count=min(frames,Self.capacity)
        let excess=max(0,available+count-Self.capacity)
        readIndex=(readIndex+excess)%Self.capacity; available-=excess; overflowFrames+=excess
        let writeIndex=(readIndex+available)%Self.capacity
        for i in 0..<count { let index=((writeIndex+i)%Self.capacity)*2; samples[index]=left[i*stride]; samples[index+1]=right[i*stride] }
        available+=count
    }
    func render(_ frames:Int) -> AVAudioEngineManualRenderingStatus {
        output.frameLength=AVAudioFrameCount(frames)
        var error:OSStatus=0
        return renderBlock(AVAudioFrameCount(frames),output.mutableAudioBufferList,&error)
    }
}

final class KaraokeAudio {
    private let queue=DispatchQueue(label:"local.tameslist.ntu-vpn.karaoke",qos:.userInitiated)
    private var tap:AudioObjectID=0, aggregate:AudioObjectID=0
    private var io:AudioDeviceIOProcID?, ioBlock:AudioDeviceIOBlock?
    private var dsp:KaraokeDSP?
    private var outputID:AudioObjectID=0, selectedProcesses:[AudioObjectID]=[]
    private var tapBuffers=1, generation=0
    private var outputListener:AudioObjectPropertyListenerBlock?, processListener:AudioObjectPropertyListenerBlock?, formatListener:AudioObjectPropertyListenerBlock?
    private let running=Atomic<Bool>(false), hasSignal=Atomic<Bool>(false), reportedError=Atomic<Bool>(false)
    private let pitch=Atomic<Int>(0)
    private var lastRenderedKey=0
    var onState:((Bool,Bool,String)->Void)?
    private func notify(_ active:Bool,_ pending:Bool,_ message:String) { DispatchQueue.main.async { [weak self] in self?.onState?(active,pending,message) } }
    func setPitch(_ value:Int) {
        let key=max(-12,min(12,value)); pitch.store(key,ordering:.relaxed)
        queue.async { [weak self] in self?.dsp?.pitchUnit.pitch=Float(key*100) }
    }
    func start() {
        notify(false,true,"Starting Safari audio…")
        queue.async { [weak self] in
            guard let self=self else { return }
            self.stopInternal(); self.generation+=1
            let token=self.generation
            do {
                try self.startInternal()
                self.notify(true,false,"Waiting for Safari audio…")
                self.queue.asyncAfter(deadline:.now()+8) { [weak self] in
                    guard let self=self, self.generation==token, self.running.load(ordering:.relaxed), !self.hasSignal.load(ordering:.relaxed) else { return }
                    self.stopInternal()
                    self.notify(false,false,"No audio received. Allow audio capture for NTU VPN in System Settings → Privacy & Security, play Safari audio, then try again.")
                }
            } catch { self.stopInternal(); self.notify(false,false,error.localizedDescription) }
        }
    }
    func stop() { notify(false,true,"Restoring normal Safari audio…"); queue.async { [weak self] in self?.stopInternal(); self?.notify(false,false,"Off · normal Safari audio") } }
    func stopSync() { queue.sync { stopInternal() } }
    private func startInternal() throws {
        selectedProcesses=try safariAudioProcesses()
        guard !selectedProcesses.isEmpty else { throw KaraokeFailure(message:"Play audio in Safari first, then turn Karaoke on.") }
        outputID=try audioUInt(AudioObjectID(kAudioObjectSystemObject),kAudioHardwarePropertyDefaultOutputDevice)
        let outputUID=try audioString(outputID,kAudioDevicePropertyDeviceUID)
        let hardwareInputs=try audioIDs(outputID,kAudioDevicePropertyStreams,kAudioObjectPropertyScopeInput)
        guard hardwareInputs.isEmpty else { throw KaraokeFailure(message:"Choose a stereo output without microphone inputs, then try again.") }
        let description=CATapDescription(processes:selectedProcesses,deviceUID:outputUID,stream:0)
        description.name="NTU VPN · Karaoke · Safari"; description.uuid=UUID(); description.isPrivate=true
        description.muteBehavior = .mutedWhenTapped
        try audioCheck(AudioHardwareCreateProcessTap(description,&tap),"Creating Safari audio tap")
        var address=audioAddress(kAudioTapPropertyFormat), format=AudioStreamBasicDescription(), bytes=UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        try audioCheck(AudioObjectGetPropertyData(tap,&address,0,nil,&bytes,&format),"Reading Safari audio format")
        guard format.mFormatID==kAudioFormatLinearPCM, format.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              format.mBitsPerChannel==32, (1...2).contains(format.mChannelsPerFrame) else { throw KaraokeFailure(message:"Choose a mono or stereo output with standard floating-point audio.") }
        tapBuffers=format.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0 ? Int(format.mChannelsPerFrame) : 1
        dsp=try KaraokeDSP(sampleRate:format.mSampleRate)
        dsp?.setPitch(pitch.load(ordering:.relaxed))
        let device:[String:Any]=[
            kAudioAggregateDeviceNameKey:"NTU VPN Karaoke", kAudioAggregateDeviceUIDKey:"local.tameslist.ntu-vpn.karaoke.\(UUID().uuidString)",
            kAudioAggregateDeviceIsPrivateKey:true,kAudioAggregateDeviceIsStackedKey:false,
            kAudioAggregateDeviceTapAutoStartKey:true,kAudioAggregateDeviceMainSubDeviceKey:outputUID,
            kAudioAggregateDeviceSubDeviceListKey:[[kAudioSubDeviceUIDKey:outputUID]],
            kAudioAggregateDeviceTapListKey:[[kAudioSubTapUIDKey:description.uuid.uuidString,kAudioSubTapDriftCompensationKey:true,kAudioSubTapDriftCompensationQualityKey:kAudioAggregateDriftCompensationMaxQuality]]
        ]
        try audioCheck(AudioHardwareCreateAggregateDevice(device as CFDictionary,&aggregate),"Creating temporary audio route")
        var alive=false
        for _ in 0..<30 { if (try? audioUInt(aggregate,kAudioDevicePropertyDeviceIsAlive))==1 { alive=true; break }; Thread.sleep(forTimeInterval:0.1) }
        guard alive else { throw KaraokeFailure(message:"The temporary audio route did not become ready.") }
        hasSignal.store(false,ordering:.relaxed); reportedError.store(false,ordering:.relaxed)
        ioBlock = { [unowned self] _,input,_,output,_ in self.render(input,output) }
        try audioCheck(AudioDeviceCreateIOProcIDWithBlock(&io,aggregate,nil,ioBlock!),"Connecting Safari audio")
        running.store(true,ordering:.relaxed)
        try audioCheck(AudioDeviceStart(aggregate,io),"Starting Safari audio")
        addListeners()
    }
    private func render(_ input:UnsafePointer<AudioBufferList>,_ output:UnsafeMutablePointer<AudioBufferList>) {
        let outputs=UnsafeMutableAudioBufferListPointer(output)
        for buffer in outputs { if let data=buffer.mData { memset(data,0,Int(buffer.mDataByteSize)) } }
        guard running.load(ordering:.relaxed),let dsp=dsp,!outputs.isEmpty else { return }
        let inputs=UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating:input))
        guard inputs.count>=tapBuffers else { return }
        let first=inputs.count-tapBuffers, left=inputs[first]
        let channels=max(1,Int(left.mNumberChannels))
        guard let leftData=left.mData?.assumingMemoryBound(to:Float.self) else { return }
        let rightData:UnsafePointer<Float>
        if channels>=2 { rightData=UnsafePointer(leftData.advanced(by:1)) }
        else if tapBuffers>=2,let other=inputs[first+1].mData { rightData=UnsafePointer(other.assumingMemoryBound(to:Float.self)) }
        else { rightData=UnsafePointer(leftData) }
        let inputFrames=Int(left.mDataByteSize)/(channels*MemoryLayout<Float>.size)
        let frames=min(Int(outputs[0].mDataByteSize)/(max(1,Int(outputs[0].mNumberChannels))*MemoryLayout<Float>.size),KaraokeDSP.maximumFrames)
        guard frames>0, inputFrames>0 else { return }
        let count=min(inputFrames,frames)
        if !hasSignal.load(ordering:.relaxed) {
            var peak:Float=0
            for i in 0..<count { peak=max(peak,abs(leftData[i*channels]),abs(rightData[i*channels])) }
            if peak>0.00001 && !hasSignal.exchange(true,ordering:.relaxed) { notify(true,false,"On · Safari audio · same playback speed") }
        }
        let key=pitch.load(ordering:.relaxed)
        if lastRenderedKey==0 && key != 0 {
            dsp.readIndex=0; dsp.available=0
            AudioUnitReset(dsp.pitchUnit.audioUnit,kAudioUnitScope_Global,0)
        }
        lastRenderedKey=key
        if key==0 { writeStereo(UnsafePointer(leftData),rightData,stride:channels,frames:count,to:outputs); return }
        dsp.push(UnsafePointer(leftData),rightData,frames:count,stride:channels)
        let status=dsp.render(frames)
        guard status == .success,let data=dsp.output.floatChannelData else {
            if !reportedError.exchange(true,ordering:.relaxed) {
                queue.async { [weak self] in self?.stopInternal(); self?.notify(false,false,"Audio processing stopped. Normal Safari audio restored; try turning Karaoke on again.") }
            }
            return
        }
        writeStereo(UnsafePointer(data[0]),UnsafePointer(data[1]),stride:1,frames:frames,to:outputs)
    }
    private func writeStereo(_ left:UnsafePointer<Float>,_ right:UnsafePointer<Float>,stride:Int,frames:Int,to outputs:UnsafeMutableAudioBufferListPointer) {
        for b in 0..<outputs.count {
            guard let data=outputs[b].mData?.assumingMemoryBound(to:Float.self) else { continue }
            let channels=max(1,Int(outputs[b].mNumberChannels)), capacity=Int(outputs[b].mDataByteSize)/(channels*MemoryLayout<Float>.size)
            for i in 0..<min(frames,capacity) {
                if channels==1 { data[i]=outputs.count>1 ? (b==0 ? left[i*stride] : right[i*stride]) : (left[i*stride]+right[i*stride])*0.5 }
                else { data[i*channels]=left[i*stride]; data[i*channels+1]=right[i*stride] }
            }
        }
    }
    private func addListeners() {
        let system=AudioObjectID(kAudioObjectSystemObject)
        outputListener={ [weak self] _,_ in
            guard let self=self,self.running.load(ordering:.relaxed),let current=try? audioUInt(system,kAudioHardwarePropertyDefaultOutputDevice),current != self.outputID else { return }
            self.stopInternal(); self.notify(false,false,"Sound output changed · turn Karaoke on again.")
        }
        processListener={ [weak self] _,_ in
            guard let self=self,self.running.load(ordering:.relaxed),let current=try? audioIDs(system,kAudioHardwarePropertyProcessObjectList),!Set(self.selectedProcesses).isSubset(of:Set(current)) else { return }
            self.stopInternal(); self.notify(false,false,"Safari audio restarted · turn Karaoke on again.")
        }
        formatListener={ [weak self] _,_ in
            guard let self=self,self.running.load(ordering:.relaxed) else { return }
            self.stopInternal(); self.notify(false,false,"Audio format changed · turn Karaoke on again.")
        }
        var output=audioAddress(kAudioHardwarePropertyDefaultOutputDevice), processes=audioAddress(kAudioHardwarePropertyProcessObjectList)
        AudioObjectAddPropertyListenerBlock(system,&output,queue,outputListener!)
        AudioObjectAddPropertyListenerBlock(system,&processes,queue,processListener!)
        var format=audioAddress(kAudioDevicePropertyNominalSampleRate)
        AudioObjectAddPropertyListenerBlock(outputID,&format,queue,formatListener!)
    }
    private func stopInternal() {
        generation+=1; running.store(false,ordering:.relaxed)
        let system=AudioObjectID(kAudioObjectSystemObject)
        var output=audioAddress(kAudioHardwarePropertyDefaultOutputDevice),processes=audioAddress(kAudioHardwarePropertyProcessObjectList)
        if let listener=outputListener { AudioObjectRemovePropertyListenerBlock(system,&output,queue,listener) }; outputListener=nil
        if let listener=processListener { AudioObjectRemovePropertyListenerBlock(system,&processes,queue,listener) }; processListener=nil
        if let listener=formatListener { var format=audioAddress(kAudioDevicePropertyNominalSampleRate); AudioObjectRemovePropertyListenerBlock(outputID,&format,queue,listener) }; formatListener=nil
        if let io=io { AudioDeviceStop(aggregate,io); AudioDeviceDestroyIOProcID(aggregate,io) }; io=nil; ioBlock=nil
        if aggregate != 0 { AudioHardwareDestroyAggregateDevice(aggregate); aggregate=0 }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap); tap=0 }
        dsp=nil; selectedProcesses=[]; lastRenderedKey=0
    }
}
